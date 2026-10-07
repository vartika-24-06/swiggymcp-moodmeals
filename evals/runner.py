"""The eval runner: scenarios x strategies x runs, a hard spend cap, committed results
(tasks T7.3; design 13.4; requirements E1, E2).

    python -m evals.runner --set smoke --strategies agent,fixed_workflow --provider groq \\
        --model openai/gpt-oss-20b --runs 1

The API key comes from the provider's environment variable (see KEY_ENV) and is never printed
or saved. Everything runs on the synthetic mock world. Spend is estimated from token counts
and the price table (`moodmeals/models/pricing.py`), converted to rupees, and checked BEFORE
every model call, so a run overshoots the cap by at most one call. When the cap is hit the
run in progress is dropped from the scores, the eval stops, and the results so far are still
written. Results include failures (E1) and report counts as "k of n".
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from evals.run import STRATEGIES, run_strategy
from evals.scenario import Scenario, ScenarioError, load_scenarios
from evals.scoring import Score, k_of_n, score_run, summarise
from moodmeals.models.adapters import make_client
from moodmeals.models.demo import DemoLLM
from moodmeals.models.llm import LLMError
from moodmeals.models.pricing import LAST_CHECKED as PRICES_CHECKED
from moodmeals.models.pricing import estimate_run_cost, price_for
from moodmeals.models.schema import PROMPT_VERSION

RESULTS_DIR = Path(__file__).parent / "results"
DEFAULT_CAP_INR = 1500.0
# Rupees per US dollar. A config value, not a fact: check it and override with --usd-inr.
USD_INR = 88.0
USD_INR_CHECKED = "2026-10-07 (unverified)"
KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
RESULTS_VERSION = 2
# Groq's free tier limits tokens per minute and one agent turn sends about 2.5k tokens, so
# pace the calls. Other providers default to no pause. Override with --min-interval-s.
DEFAULT_MIN_INTERVAL_S = {"groq": 20.0}
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT_S = 30.0
MAX_CONSECUTIVE_INVALID = 3  # then the model is treated as unreachable and the eval stops
MAX_TRACE_EVENTS = 80  # events kept for a failed run (mock data only)


class SpendCapReached(LLMError):
    """Raised before a model call that would start beyond the cap. The loop ends the run."""


class SpendMeter:
    """Dollars spent so far, across runs. The cap is in rupees (E2)."""

    def __init__(self, cap_inr: float, usd_inr: float) -> None:
        self.cap_usd = cap_inr / usd_inr
        self.usd_inr = usd_inr
        self.done_usd = 0.0  # finished runs
        self.hit = False

    def spent_usd(self, live: Any = None) -> float:
        cost = live.cost_estimate() if live is not None and hasattr(live, "cost_estimate") else 0
        return self.done_usd + (cost or 0.0)

    @property
    def done_inr(self) -> float:
        return self.done_usd * self.usd_inr

    def exhausted(self, live: Any = None) -> bool:
        return self.spent_usd(live) >= self.cap_usd


class CappedClient:
    """Wraps a model client: refuses to start a call once the cap is reached."""

    def __init__(self, inner: Any, meter: SpendMeter) -> None:
        self._inner, self._meter = inner, meter
        for attr in ("provider", "model"):
            if hasattr(inner, attr):
                setattr(self, attr, getattr(inner, attr))

    @property
    def paused_s(self) -> float:
        return getattr(self._inner, "paused_s", 0.0)

    def next_action(self, view: dict[str, Any]) -> Any:
        if self._meter.exhausted(self._inner):
            self._meter.hit = True
            raise SpendCapReached("spend cap reached")
        return self._inner.next_action(view)

    def usage(self) -> Any:
        return self._inner.usage()

    def cost_estimate(self) -> float | None:
        return self._inner.cost_estimate()


class RetryingClient:
    """Paces model calls and waits out rate limits, so a busy free tier does not turn into
    "agent failures". Only a rate-limit error is retried, with a growing wait."""

    def __init__(
        self,
        inner: Any,
        *,
        min_interval_s: float = 0.0,
        retries: int = RATE_LIMIT_RETRIES,
        wait_s: float = RATE_LIMIT_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner, self._min, self._retries, self._wait = inner, min_interval_s, retries, wait_s
        self._sleep, self._clock = sleep, clock
        self._last: float | None = None
        self.paused_s = 0.0  # time spent waiting for quota: not the agent's time (see eval_guard)
        for attr in ("provider", "model"):
            if hasattr(inner, attr):
                setattr(self, attr, getattr(inner, attr))

    def _pause(self, seconds: float) -> None:
        self._sleep(seconds)
        self.paused_s += seconds

    def next_action(self, view: dict[str, Any]) -> Any:
        for attempt in range(self._retries + 1):
            if self._last is not None:
                gap = self._min - (self._clock() - self._last)
                if gap > 0:
                    self._pause(gap)
            self._last = self._clock()
            try:
                return self._inner.next_action(view)
            except LLMError as e:
                if not str(e).startswith("rate_limited") or attempt == self._retries:
                    raise
                self._pause(self._wait * (attempt + 1))
        raise AssertionError("unreachable")

    def usage(self) -> Any:
        return self._inner.usage()

    def cost_estimate(self) -> float | None:
        return self._inner.cost_estimate()


def infra_error(rec: Any) -> str | None:
    """Why a run says nothing about the agent: the model could not be reached or answered
    (rate limit, bad key, network). Such runs are recorded but not scored."""
    for e in rec.events:
        if e.type == "error" and e.payload.get("kind") == "model_error":
            return str(e.payload.get("detail", "model_error"))[:300]
    for note in rec.notes:
        if note.startswith("model_error"):
            return note[:300]
    return None


def git_info() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=10, cwd=Path(__file__).parent
        ).stdout.strip()

    try:
        return {"commit": run("rev-parse", "--short", "HEAD") or "unknown",
                "dirty": bool(run("status", "--porcelain"))}  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return {"commit": "unknown", "dirty": None}


def _trace(rec: Any) -> list[dict[str, Any]]:
    return [
        {"step": e.step, "type": e.type, "actor": e.actor, "payload": e.payload,
         "rationale": e.rationale}
        for e in rec.events[:MAX_TRACE_EVENTS]
    ]  # fmt: skip


def run_eval(
    scenarios: list[Scenario],
    strategies: list[str],
    client_factory: Callable[[], Any] | None,
    *,
    runs: int = 1,
    cap_inr: float = DEFAULT_CAP_INR,
    usd_inr: float = USD_INR,
    meta: dict[str, Any] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run every scenario with every strategy and return the results document.

    `client_factory` makes a fresh model client per run (clean usage and cost); it may be None
    when only `fixed_workflow` is run. Deterministic strategies run once whatever `runs` says.
    """
    say = progress or (lambda _msg: None)
    meter = SpendMeter(cap_inr, usd_inr)
    scores: list[Score] = []
    rows: list[dict[str, Any]] = []
    aborted: dict[str, Any] | None = None
    invalid = consecutive_invalid = 0
    unreachable = False

    for sc in scenarios:
        for strategy in strategies:
            for n in range(1, (1 if strategy == "fixed_workflow" else runs) + 1):
                if meter.exhausted():
                    meter.hit = True
                if meter.hit:
                    break
                client = None
                if strategy != "fixed_workflow":
                    client = CappedClient(client_factory(), meter) if client_factory else None
                rec = run_strategy(strategy, sc, client)
                meter.done_usd += rec.cost_usd or 0.0  # an interrupted run still cost money
                why = None if meter.hit else infra_error(rec)
                if why and strategy != "fixed_workflow":
                    invalid += 1
                    consecutive_invalid += 1
                    rows.append({"scenario_id": sc.id, "group": sc.group, "strategy": strategy,
                                 "run": n, "invalid": True, "invalid_reason": why})  # fmt: skip
                    say(f"{sc.id} {strategy} run {n}: INVALID, not scored ({why})")
                    if consecutive_invalid >= MAX_CONSECUTIVE_INVALID:
                        unreachable = True
                        break
                    continue
                consecutive_invalid = 0
                if meter.hit:  # the cap stopped this run partway: not a model failure
                    aborted = {"scenario_id": sc.id, "strategy": strategy, "run": n}
                    say(f"{sc.id} {strategy} run {n}: stopped by the spend cap, not scored")
                    break
                score = score_run(rec, sc)
                scores.append(score)
                row: dict[str, Any] = {
                    "scenario_id": sc.id, "group": sc.group, "strategy": strategy, "run": n,
                    "passed": score.passed, "failed_checks": score.failed,
                    "checks": score.checks, "metrics": score.metrics, "notes": score.notes,
                }  # fmt: skip
                if not score.passed:
                    row["trace"] = _trace(rec)  # failures are published (E1)
                rows.append(row)
                say(
                    f"{sc.id} {strategy} run {n}: "
                    + ("passed" if score.passed else f"FAILED {', '.join(score.failed)}")
                    + f" (spent so far ₹{meter.done_inr:.2f})"
                )
            if meter.hit or unreachable:
                break
        if meter.hit or unreachable:
            break

    summary = summarise(scores)
    doc: dict[str, Any] = {
        "version": RESULTS_VERSION,
        "date": datetime.date.today().isoformat(),
        **(meta or {}),
        "strategies": strategies,
        "runs_per_scenario": runs,
        "scenarios": [s.id for s in scenarios],
        "cap_inr": cap_inr,
        "usd_inr": usd_inr,
        "usd_inr_checked": USD_INR_CHECKED,
        "prices_checked": PRICES_CHECKED,
        "spent_inr": round(meter.done_inr, 4),
        "aborted_by_cap": meter.hit,
        "aborted_run": aborted,
        "invalid_runs": invalid,
        "aborted_unreachable": unreachable,
        "n_runs": len(rows),
        "summary": {
            strat: {name: {**cell, "text": k_of_n(cell)} for name, cell in cells.items()}
            for strat, cells in summary.items()
        },
        "results": rows,
    }
    return doc


def results_path(out_dir: Path, doc: dict[str, Any]) -> Path:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(doc.get("model") or "unknown")).strip("-")
    base = f"{doc['date']}-{slug}"
    path = out_dir / f"{base}.json"
    n = 2
    while path.exists():  # never overwrite an earlier results file
        path = out_dir / f"{base}-{n}.json"
        n += 1
    return path


def write_results(doc: dict[str, Any], out_dir: Path = RESULTS_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = results_path(out_dir, doc)
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- command line


def build_factory(provider: str, model: str) -> Callable[[], Any]:
    if provider == "demo":
        return DemoLLM
    env = KEY_ENV.get(provider)
    if env is None:
        raise SystemExit(f"Unknown provider {provider}. Use demo, {', '.join(KEY_ENV)}.")
    key = os.environ.get(env, "").strip()
    if not key:
        raise SystemExit(f"Set {env} in this terminal first (the key is never saved).")
    if not model:
        raise SystemExit("--model is required for a real provider.")
    return lambda: make_client(provider, model, key)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="python -m evals.runner", description=__doc__.split("\n")[0])
    ap.add_argument("--set", choices=("smoke", "full"), default="smoke")
    ap.add_argument("--strategies", default="agent,fixed_workflow",
                    help=f"comma list of {', '.join(STRATEGIES)}")  # fmt: skip
    ap.add_argument("--provider", default="demo", help="demo, " + ", ".join(KEY_ENV))
    ap.add_argument("--model", default="")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--only", default="", help="comma list of scenario ids, e.g. S-04,S-06")
    ap.add_argument("--cap-inr", type=float, default=DEFAULT_CAP_INR)
    ap.add_argument("--usd-inr", type=float, default=USD_INR)
    ap.add_argument(
        "--min-interval-s",
        type=float,
        default=None,
        help="pause between model calls (default 20 for groq, 0 otherwise)",
    )
    ap.add_argument("--rate-limit-retries", type=int, default=RATE_LIMIT_RETRIES)
    ap.add_argument("--rate-limit-wait-s", type=float, default=RATE_LIMIT_WAIT_S)
    ap.add_argument("--out", type=Path, default=RESULTS_DIR)
    ap.add_argument(
        "--dry-plan", action="store_true", help="print the plan and estimate, run nothing"
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    bad = [s for s in strategies if s not in STRATEGIES]
    if bad or not strategies:
        raise SystemExit(
            f"Unknown strategy: {', '.join(bad) or '(none)'}. Use {', '.join(STRATEGIES)}."
        )
    if args.runs < 1 or args.cap_inr <= 0 or args.usd_inr <= 0:
        raise SystemExit("--runs, --cap-inr and --usd-inr must be positive.")
    try:
        scenarios = load_scenarios(smoke_only=args.set == "smoke")
    except ScenarioError as e:
        raise SystemExit(f"Scenario problem: {e}") from None
    if args.only.strip():
        wanted = [x.strip().upper() for x in args.only.split(",") if x.strip()]
        known = {s.id: s for s in load_scenarios()}
        unknown = [x for x in wanted if x not in known]
        if unknown:
            raise SystemExit(
                f"Unknown scenario id: {', '.join(unknown)}. Known: {', '.join(known)}."
            )
        scenarios = [known[x] for x in sorted(set(wanted))]
    uses_model = any(s != "fixed_workflow" for s in strategies)
    provider, model = (
        args.provider,
        args.model or ("scripted-demo" if args.provider == "demo" else ""),
    )

    est_usd: float | None = 0.0
    if uses_model and provider != "demo":
        if price_for(provider, model) is None:
            raise SystemExit(
                f"No price for {provider}/{model}, so the cap cannot be enforced. "
                "Add it to moodmeals/models/pricing.py first."
            )
        per_run = estimate_run_cost(provider, model)
        n_model = sum(1 for s in strategies if s != "fixed_workflow") * len(scenarios) * args.runs
        est_usd = (per_run or 0.0) * n_model
    n_runs = sum((1 if s == "fixed_workflow" else args.runs) * len(scenarios) for s in strategies)
    print(f"Plan: {len(scenarios)} scenarios x {', '.join(strategies)} = {n_runs} runs "
          f"({args.set} set, model {model or 'none'})")  # fmt: skip
    print(f"Rough cost estimate: ₹{(est_usd or 0) * args.usd_inr:.2f} (cap ₹{args.cap_inr:.0f}; "
          f"rate ₹{args.usd_inr}/USD, {USD_INR_CHECKED}; an estimate only)")  # fmt: skip
    if est_usd is not None and est_usd * args.usd_inr > args.cap_inr:
        print("Warning: the estimate is above the cap, so the run will stop at the cap.")
    if args.dry_plan:
        return 0

    inner = build_factory(provider, model) if uses_model else None
    interval = (
        args.min_interval_s
        if args.min_interval_s is not None
        else DEFAULT_MIN_INTERVAL_S.get(provider, 0.0)
    )
    factory = None
    if inner is not None:
        factory = lambda: RetryingClient(  # noqa: E731
            inner(),
            min_interval_s=interval,
            retries=args.rate_limit_retries,
            wait_s=args.rate_limit_wait_s,
        )
        if interval:
            print(f"Pacing model calls at least {interval:.0f}s apart; this will take a while.")
    meta = {"provider": provider, "model": model or "fixed-workflow", "set": args.set,
            "prompt_version": PROMPT_VERSION, "git": git_info(),
            "temperature": "provider default (not set by the runner)"}  # fmt: skip
    doc = run_eval(scenarios, strategies, factory, runs=args.runs, cap_inr=args.cap_inr,
                   usd_inr=args.usd_inr, meta=meta, progress=print)  # fmt: skip
    path = write_results(doc, args.out)
    print(f"\nWrote {path}")
    for strat, cells in doc["summary"].items():
        print(f"{strat}: all checks {cells['all_checks']['text']}")
    if doc["invalid_runs"]:
        print(f"{doc['invalid_runs']} run(s) were invalid (model unreachable) and are not scored.")
    if doc["aborted_unreachable"]:
        print("STOPPED: the model failed to answer several runs in a row. Check the key, the "
              "model name and the rate limits, then rerun. Partial results saved.")  # fmt: skip
        return 4
    if doc["aborted_by_cap"]:
        print(f"STOPPED by the spend cap (₹{doc['spent_inr']:.2f} spent). Partial results saved.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
