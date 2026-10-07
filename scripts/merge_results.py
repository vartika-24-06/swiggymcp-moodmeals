# ruff: noqa: E501
"""Merge parts of one eval run into a single results file (tasks T7.5).

A long run can be split by Ctrl+C, a network drop or a rate limit. This builds one results
file from chosen scenarios of each part, drops invalid runs, checks that every scenario has the
expected runs, and recomputes the "k of n" summary. Nothing is guessed: a part must carry the
scenario's rows, and a shortfall is reported, not hidden.

    python scripts/merge_results.py OUT.json PART1.json=S-01:S-17 PART2.json=S-18 PART3.json=S-19:S-24

A range is `S-01:S-17`; scenarios are comma-separated (`S-01:S-05,S-09`).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.scenario import load_scenarios  # noqa: E402
from evals.scoring import Score, fallback_plan_ok, k_of_n, summarise  # noqa: E402

EXPECTED = {"agent": 3, "agent_no_validator": 3, "fixed_workflow": 1}  # per scenario, runs=3
# Runs whose slowest model call (or total model time, for results written before per-call
# latency was kept) is above this are infrastructure failures: a laptop asleep, a network stall.
STALL_MS = 600_000


class MergeError(ValueError):
    pass


def expand(spec: str) -> list[str]:
    """`S-01:S-03,S-09` -> ['S-01', 'S-02', 'S-03', 'S-09']."""
    out: list[str] = []
    for part in spec.split(","):
        part = part.strip().upper()
        m = re.fullmatch(r"S-(\d{2})(?::S-(\d{2}))?", part)
        if not m:
            raise MergeError(f"bad scenario spec: {part!r}")
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if hi < lo:
            raise MergeError(f"empty range: {part}")
        out += [f"S-{i:02d}" for i in range(lo, hi + 1)]
    return out


def rescore_fallback(rows: list[dict[str, Any]], expect: dict[str, list[str]]) -> list[str]:
    """Re-judge `fallback_plan` for failed rows from the plan stored in their trace, under the
    current word lists. Passed rows passed a stricter list, so they stay passed. Returns the
    "scenario strategy run" labels that changed."""
    changed: list[str] = []
    for r in rows:
        must = expect.get(r["scenario_id"], [])
        if r.get("checks", {}).get("fallback_plan") is not False or not must:
            continue
        plan = next(
            (e["payload"] for e in reversed(r.get("trace", [])) if e["type"] == "plan"), None
        )
        if plan is None:
            continue
        names = [i.get("name", "") for i in plan.get("items", [])]
        if fallback_plan_ok(str(plan.get("reason", "")), names, must):
            r["checks"]["fallback_plan"] = True
            r["failed_checks"] = [c for c in r["failed_checks"] if c != "fallback_plan"]
            r["passed"] = not r["failed_checks"]
            r["rescored"] = ["fallback_plan"]
            if r["passed"]:
                r.pop("trace", None)
            changed.append(f"{r['scenario_id']} {r['strategy']} {r['run']}")
    return changed


def rescore_plan_size(rows: list[dict[str, Any]], limits: dict[str, int]) -> list[str]:
    """Re-judge a failed `plan_size` from the stored plan under the scenario's current maximum
    (limits: scenario id -> max total quantity). Returns the labels that changed."""
    changed: list[str] = []
    for r in rows:
        hi = limits.get(r["scenario_id"])
        if r.get("checks", {}).get("plan_size") is not False or hi is None:
            continue
        plan = next(
            (e["payload"] for e in reversed(r.get("trace", [])) if e["type"] == "plan"), None
        )
        if plan is None:
            continue
        if sum(int(i.get("qty", 1)) for i in plan.get("items", [])) <= hi:
            r["checks"]["plan_size"] = True
            r["failed_checks"] = [c for c in r["failed_checks"] if c != "plan_size"]
            r["passed"] = not r["failed_checks"]
            r["rescored"] = [*r.get("rescored", []), "plan_size"]
            if r["passed"]:
                r.pop("trace", None)
            changed.append(f"{r['scenario_id']} {r['strategy']} {r['run']}")
    return changed


def merge(
    parts: list[tuple[dict[str, Any], list[str], str]],
    expected: dict[str, int] | None = None,
    all_scenarios: list[str] | None = None,
    stall_ms: int = STALL_MS,
    plan_must_include: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """`parts` is (results document, scenario ids to take, source name)."""
    expected = EXPECTED if expected is None else expected
    excluded: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc, ids, name in parts:
        dup = seen & set(ids)
        if dup:
            raise MergeError(f"scenario taken from two parts: {', '.join(sorted(dup))}")
        seen |= set(ids)
        take = []
        for r in doc["results"]:
            if r["scenario_id"] not in ids or r.get("invalid"):
                continue
            mt = r.get("metrics", {})
            slow = mt.get("slowest_call_ms") or mt.get("latency_ms", 0)
            if r["strategy"] != "fixed_workflow" and slow > stall_ms:
                excluded.append({"scenario_id": r["scenario_id"], "strategy": r["strategy"],
                                 "run": r["run"], "source": name,
                                 "reason": f"a model call took {slow // 1000}s (stalled: laptop "
                                           "asleep or network down), not scored"})  # fmt: skip
                continue
            take.append(r)
        rows += take
        sources.append({
            "file": name, "scenarios": sorted(ids), "git_commit": doc.get("git", {}).get("commit"),
            "runs_taken": len(take), "spent_inr": doc.get("spent_inr"),
            "invalid_runs_in_part": doc.get("invalid_runs", 0),
        })  # fmt: skip
    problems = []
    for sid in sorted(seen):
        c = Counter(r["strategy"] for r in rows if r["scenario_id"] == sid)
        gone = Counter(x["strategy"] for x in excluded if x["scenario_id"] == sid)
        for strat, want in expected.items():
            if c[strat] + gone[strat] != want:
                problems.append(f"{sid} {strat}: {c[strat]} valid runs, expected {want}")
    if all_scenarios and set(all_scenarios) - seen:
        problems.append("missing scenarios: " + ", ".join(sorted(set(all_scenarios) - seen)))
    if problems:
        raise MergeError("; ".join(problems))
    must = plan_must_include
    if must is None:
        must = {s.id: list(s.expect.plan_must_include) for s in load_scenarios()}
    rescored = rescore_fallback(rows, must)
    limits = {
        s.id: s.expect.max_items_qty for s in load_scenarios() if s.expect.max_items_qty
    }
    rescored_size = rescore_plan_size(rows, limits)
    rows.sort(key=lambda r: (r["scenario_id"], list(expected).index(r["strategy"]), r["run"]))
    scores = [
        Score(r["scenario_id"], r["strategy"], r["checks"], r.get("metrics", {})) for r in rows
    ]
    first = parts[0][0]
    base = {k: first[k] for k in (
        "version", "date", "provider", "model", "set", "prompt_version", "temperature",
        "strategies", "runs_per_scenario", "cap_inr", "usd_inr", "usd_inr_checked",
        "prices_checked",
    ) if k in first}  # fmt: skip
    models = {d.get("model") for d, _, _ in parts}
    prompts = {d.get("prompt_version") for d, _, _ in parts}
    if len(models) > 1 or len(prompts) > 1:
        raise MergeError(f"parts differ in model or prompt: {models} {prompts}")
    return {
        **base,
        "scenarios": sorted(seen),
        "merged_from": sources,
        "spent_inr": round(sum(s["spent_inr"] or 0 for s in sources), 4),
        "aborted_by_cap": False, "aborted_unreachable": False, "interrupted": False,
        "invalid_runs": 0, "n_runs": len(rows),
        "excluded_runs": excluded, "rescored_fallback_plan": rescored,
        "rescored_plan_size": rescored_size,
        "summary": {
            strat: {n: {**cell, "text": k_of_n(cell)} for n, cell in cells.items()}
            for strat, cells in summarise(scores).items()
        },
        "results": rows,
    }  # fmt: skip


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    out, parts = Path(argv[1]), []
    for arg in argv[2:]:
        path, _, spec = arg.partition("=")
        if not spec:
            print(f"each part needs FILE=SCENARIOS, got {arg!r}")
            return 2
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        parts.append((doc, expand(spec), Path(path).name))
    every = sorted({s for _, ids, _ in parts for s in ids})
    try:
        merged = merge(parts, all_scenarios=every)
    except MergeError as e:
        print(f"Not merged: {e}")
        return 1
    out.write_text(json.dumps(merged, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {out}: {merged['n_runs']} runs, {len(merged['scenarios'])} scenarios, "
          f"spent ₹{merged['spent_inr']}")  # fmt: skip
    for x in merged["excluded_runs"]:
        print(f"  excluded {x['scenario_id']} {x['strategy']} run {x['run']}: {x['reason']}")
    if merged["rescored_plan_size"]:
        print("  plan_size re-scored from traces: " + ", ".join(merged["rescored_plan_size"]))
    if merged["rescored_fallback_plan"]:
        print(
            "  fallback_plan re-scored from traces: " + ", ".join(merged["rescored_fallback_plan"])
        )
    for strat, cells in merged["summary"].items():
        print(f"  {strat}: all checks {cells['all_checks']['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
