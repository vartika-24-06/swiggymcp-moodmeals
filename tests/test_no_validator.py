# ruff: noqa: E501
"""The `agent_no_validator` strategy (tasks T7.5): the same loop with the plan validator off,
to show what the validator catches. Nothing here calls a model."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import runner  # noqa: E402
from evals.run import STRATEGIES, run_scripted, run_strategy  # noqa: E402
from evals.scenario import load_scenarios  # noqa: E402
from evals.scoring import score_run  # noqa: E402
from moodmeals.core.guard import Guard  # noqa: E402
from moodmeals.core.loop import Agent  # noqa: E402
from moodmeals.models.demo import DemoLLM  # noqa: E402
from moodmeals.models.fake import FakeLLM  # noqa: E402
from moodmeals.providers.mock import MockProvider  # noqa: E402
from moodmeals.providers.switches import Switches  # noqa: E402

SCEN = {s.id: s for s in load_scenarios()}
RID, DISH = "61093", "61093009"


def act(kind, **args):
    return {"action": kind, "args": args, "rationale": "t"}


SEARCH = act("tool_call", name="search_restaurants", params={"query": "biryani"})
MENU = act("tool_call", name="get_menu", params={"restaurant_id": RID})


def propose(item=DISH, qty=1):
    return act(
        "propose_plan",
        path="order_in",
        reason="r",
        restaurant_id=RID,
        items=[{"id": item, "qty": qty}],
    )


def agent_for(script, *, validate, switches=None):
    agent = Agent(
        FakeLLM(script), MockProvider(seed=1, switches=switches), Guard(), validate=validate
    )
    return agent, agent.start("biryani")


def test_the_validator_rejects_a_plan_from_a_closed_restaurant_and_the_ablation_accepts_it():
    closed = Switches(all_closed=True)
    on, state_on = agent_for(
        [SEARCH, MENU, propose(), propose(), propose()], validate=True, switches=closed
    )
    on.run(state_on)
    assert (
        state_on.plan is None and state_on.stop_reason == "could_not_verify"
    )  # rejected three times
    assert any(e.type == "validation" and e.payload.get("ok") is False for e in state_on.events)

    off, state_off = agent_for([SEARCH, MENU, propose()], validate=False, switches=closed)
    off.run(state_off)
    assert state_off.phase == "AWAITING_APPROVAL" and state_off.plan is not None  # sailed through
    v = next(e for e in state_off.events if e.type == "validation")
    assert v.payload == {"ok": True, "warnings": [], "validator": "off"}  # the trace says why


def test_the_default_agent_keeps_the_validator_on():
    assert Agent(FakeLLM([]), MockProvider(seed=1)).validate is True


def test_an_invented_item_reaches_the_approval_screen_without_the_validator_and_is_counted():
    ghost = [SEARCH, propose("NOPE-123")]
    sc = SCEN["S-08"]
    off = run_strategy("agent_no_validator", sc, FakeLLM(list(ghost)))
    assert off.phase == "AWAITING_APPROVAL" and off.plan.items[0].name == "unknown item"
    s_off = score_run(off, sc)
    assert s_off.checks["no_hallucinated_entities"] is False and s_off.checks["plan_valid"] is False
    assert s_off.metrics["hallucinated"] >= 1

    on = run_strategy("agent", sc, FakeLLM([*ghost, propose("NOPE-123"), propose("NOPE-123")]))
    assert on.plan is None  # the validator never let it through
    assert (
        score_run(on, sc).checks["no_hallucinated_entities"] is True
    )  # nothing invented was accepted


def test_a_budget_breaking_plan_is_caught_only_with_the_validator():
    sc = SCEN["S-14"]  # budget 150
    dear = propose(DISH, 1)  # a Rs 210 dish
    off = run_strategy("agent_no_validator", sc, FakeLLM([SEARCH, MENU, dear]))
    s = score_run(off, sc)
    assert (
        off.plan is not None
        and s.checks["hard_constraints"] is False
        and "hard_constraints" in s.failed
    )
    on = run_strategy("agent", sc, FakeLLM([SEARCH, MENU, dear, dear, dear]))
    assert on.plan is None and on.stop_reason == "could_not_verify"


def test_with_a_valid_model_the_ablation_changes_nothing():
    for sid in ("S-01", "S-03", "S-04", "S-06"):
        a = run_strategy("agent", SCEN[sid], DemoLLM())
        b = run_strategy("agent_no_validator", SCEN[sid], DemoLLM())
        assert (a.plan.path, [i.entity_id for i in a.plan.items]) == (
            b.plan.path,
            [i.entity_id for i in b.plan.items],
        ), sid
        assert b.strategy == "agent_no_validator"


def test_the_ablation_is_a_registered_model_strategy():
    assert "agent_no_validator" in STRATEGIES
    with pytest.raises(ValueError):
        run_strategy("agent_no_validator", SCEN["S-01"])  # needs a model client, like the agent
    rec = run_scripted(SCEN["S-01"], DemoLLM(), "agent_no_validator")
    assert rec.strategy == "agent_no_validator" and rec.plan is not None


def test_the_runner_reports_both_strategies_side_by_side(tmp_path, capsys):
    code = runner.main(
        ["--set", "smoke", "--strategies", "agent,agent_no_validator", "--out", str(tmp_path)]
    )
    assert code == 0
    [path] = list(tmp_path.glob("*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data["summary"]) == {"agent", "agent_no_validator"} and data["n_runs"] == 12
    assert "plan_valid" in data["summary"]["agent_no_validator"]
    assert {r["strategy"] for r in data["results"]} == {"agent", "agent_no_validator"}
