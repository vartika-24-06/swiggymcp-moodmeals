# ruff: noqa: E501
"""Record the replay gallery (tasks T8.1): mock runs only, no key, no network.

    python scripts/make_replays.py [results-file.json]

Writes `data/replays/NN-name.json`. Successes are the scripted demo model on smoke scenarios
(approved in the simulated mock world). Failures are (a) the code baseline picking the wrong
path and (b) a REAL model's failed run read from an eval results file, if one is given and
holds that trace. Everything is synthetic: no address, phone or order history is involved.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evals.fixed_workflow import FixedWorkflowLLM  # noqa: E402
from evals.run import _provider, eval_guard  # noqa: E402
from evals.scenario import load_scenarios  # noqa: E402
from moodmeals.core.loop import Agent  # noqa: E402
from moodmeals.core.replay import export_run, replay_from_trace  # noqa: E402
from moodmeals.models.demo import DemoLLM  # noqa: E402
from moodmeals.models.schema import PROMPT_VERSION  # noqa: E402

OUT = ROOT / "data" / "replays"
SCEN = {s.id: s for s in load_scenarios()}


def record(sid: str, llm, title: str, note: str, tag: str, approve: bool = True, sc=None) -> dict:
    sc = sc or SCEN[sid]
    agent = Agent(llm, _provider(sc), eval_guard(llm), strict_stop=False)
    state = agent.start(sc.user_script.opening, sc.user_script.hard_constraints())
    steps = list(sc.user_script.mid_run)
    for _ in range(25):
        agent.run(state)
        if state.waiting == "answer":
            fld = (state.pending_question or {}).get("field", "other")
            agent.provide_answer(state, sc.user_script.answers.get(fld, "no preference"))
        elif state.waiting == "address":
            options = agent.address_options(state)
            agent.choose_address(state, options[sc.user_script.address]["handle"])
        elif state.phase == "AWAITING_APPROVAL" and steps:
            step = steps.pop(0)
            if step.do == "change_constraints":
                agent.change_constraints(state, **step.with_)
            else:
                agent.reject(state)
        else:
            break
    while approve and state.phase == "AWAITING_APPROVAL":
        agent.approve(state)  # the simulated mock world: cart, then order
    return export_run(
        state, seed=sc.world.seed, model=getattr(llm, "model", "?"),
        prompt_version=PROMPT_VERSION, title=title, note=note, tag=tag,
    )  # fmt: skip


def _lower_budget(sc, budget: int):
    """S-06 with a deeper cut, so the dish visibly changes in the replay."""
    copy = sc.model_copy(deep=True)
    copy.user_script.mid_run[0].with_ = {"budget": budget}
    return copy


def failure_from_results(path: Path, sid: str, title: str, note: str) -> dict | None:
    data = json.loads(path.read_text(encoding="utf-8"))
    row = next(
        (r for r in data["results"] if r["scenario_id"] == sid and r["strategy"] == "agent"
         and r.get("trace")),
        None,
    )  # fmt: skip
    if row is None:
        return None
    return replay_from_trace(
        row["trace"], title=title, note=note, seed=SCEN[sid].world.seed,
        model=data["model"], prompt_version=data["prompt_version"], date=data["date"],
    )  # fmt: skip


def main(argv: list[str]) -> int:
    runs: list[tuple[str, dict | None]] = [
        (
            "01-tired-and-hungry",
            record(
                "S-01",
                DemoLLM(),
                "Tired and hungry: order in",
                "Low energy points to ordering in. It asks one question (diet), searches, checks the "
                "menu, and only then proposes a plan. Cart and order are two separate approvals.",
                "success",
            ),
        ),
        (
            "02-three-addresses",
            record(
                "S-03",
                DemoLLM(),
                "Three saved addresses: the picker is a question",
                "The address is never chosen for you. Picking one counts toward the three-question "
                "limit.",
                "success",
            ),
        ),
        (
            "03-everything-closed",
            record(
                "S-04",
                DemoLLM(),
                "Every restaurant is closed: a quick meal from Instamart",
                "Ordering in is impossible, so it says why and offers a ready-to-eat item instead of "
                "forcing a plan from a closed restaurant.",
                "success",
            ),
        ),
        (
            "04-budget-drops",
            record(
                "S-06",
                DemoLLM(),
                "The budget drops after the plan is shown",
                "The person lowers the budget to Rs 120 once a plan is on screen. The run updates "
                "the plan instead of restarting, and the new dish fits the new budget.",
                "success",
                sc=_lower_budget(SCEN["S-06"], 120),
            ),
        ),
        (
            "05-failure-wrong-path",
            record(
                "S-02",
                FixedWorkflowLLM(),
                "Failure: the fixed baseline cannot choose to cook",
                "The person wants to cook dal-chawal. This baseline always orders in, so it proposes a "
                "restaurant dish. It is why the real agent chooses a path.",
                "failure",
            ),
        ),
    ]
    results = Path(argv[1]) if len(argv) > 1 else None
    real = (
        failure_from_results(
            results,
            "S-04",
            "Failure (real model): gave up without trying Instamart",
            "A real model on the free tier stopped after two restaurant searches and never "
            "looked at quick meals. This led to a code rule: stop_search is refused until both "
            "restaurants and Instamart have been tried.",
        )
        if results
        else None
    )
    runs.append(("06-failure-real-model-gave-up", real))
    OUT.mkdir(parents=True, exist_ok=True)
    written = 0
    for name, data in runs:
        if data is None:
            print(f"skipped {name} (no matching trace; pass an eval results file)")
            continue
        (OUT / f"{name}.json").write_text(
            json.dumps(data, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        written += 1
        print(f"wrote data/replays/{name}.json")
    print(f"{written} replays written")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
