# ruff: noqa: E501
"""The scripted user and the three strategies (tasks T7.2). No real model: the scripted demo,
the fixed workflow and a FakeLLM stand in, so nothing costs anything."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.fixed_workflow import FixedWorkflowLLM  # noqa: E402
from evals.run import STRATEGIES, run_one_shot, run_scripted, run_strategy  # noqa: E402
from evals.scenario import load_scenarios  # noqa: E402
from evals.scoring import count_writes, score_run  # noqa: E402
from moodmeals.models.demo import DemoLLM  # noqa: E402
from moodmeals.models.fake import FakeLLM  # noqa: E402

SCEN = {s.id: s for s in load_scenarios()}


def act(kind, **args):
    return {"action": kind, "args": args, "rationale": "t"}


def test_every_smoke_scenario_runs_with_the_agent_and_never_writes():
    for sc in load_scenarios(smoke_only=True):
        rec = run_strategy("agent", sc, DemoLLM())
        assert rec.scenario_id == sc.id and rec.strategy == "agent"
        assert rec.phase in ("AWAITING_APPROVAL", "STOPPED")  # the run ends, never approved
        assert count_writes(rec) == 0
        assert rec.questions_asked <= 3


def test_scripted_user_picks_the_address_and_counts_it_as_a_question():
    rec = run_scripted(SCEN["S-03"], DemoLLM())
    picker = [
        e for e in rec.events if e.type == "question" and e.payload.get("kind") == "address_picker"
    ]
    assert picker and rec.questions_asked >= 1
    assert any(e.type == "user_input" and "address_choice" in e.payload for e in rec.events)


def test_scripted_user_answers_questions_from_the_script():
    rec = run_scripted(SCEN["S-01"], DemoLLM())
    answers = [
        e.payload["answer"] for e in rec.events if e.type == "user_input" and "answer" in e.payload
    ]
    assert answers == ["veg"]  # the demo only asks about diet; the canned reply is used
    assert rec.constraints.veg is True
    assert rec.constraints.budget is None  # never asked, so the canned budget is never given


def test_up_front_constraints_start_the_run():
    rec = run_scripted(SCEN["S-06"], DemoLLM())
    assert rec.events[0].payload["text"].startswith("Lunch for me")
    assert rec.constraints.veg is True  # stated up front in the scenario


def test_mid_run_change_is_applied_after_the_plan_and_the_run_replans():
    rec = run_scripted(SCEN["S-06"], DemoLLM())
    assert rec.mid_run_applied == 1 and rec.constraints.budget == 150
    plans = [i for i, e in enumerate(rec.events) if e.type == "plan"]
    changed = next(
        i for i, e in enumerate(rec.events) if e.type == "user_input" and "changed" in e.payload
    )
    assert plans[0] < changed < plans[-1]  # a plan, then the change, then a new plan
    score = score_run(rec, SCEN["S-06"])
    assert (
        score.checks["replanned_after_change"] is True and score.checks["hard_constraints"] is True
    )


def test_the_run_stops_at_approval_and_does_not_approve():
    rec = run_scripted(SCEN["S-01"], DemoLLM())
    assert rec.phase == "AWAITING_APPROVAL"
    assert not any(
        e.type == "approval" and e.payload.get("decision") == "approved" for e in rec.events
    )


def test_agent_with_the_demo_model_handles_the_blocked_order_scenarios():
    for sid in ("S-04", "S-05"):
        rec = run_strategy("agent", SCEN[sid], DemoLLM())
        score = score_run(rec, SCEN[sid])
        assert rec.plan is not None and rec.plan.path == "cook", sid
        assert score.passed, (sid, score.failed)  # quick meal, reason stated, trajectory ok


# ---------------------------------------------------------------- fixed_workflow


def test_fixed_workflow_orders_in_and_has_no_model_cost():
    rec = run_strategy("fixed_workflow", SCEN["S-01"])
    assert rec.strategy == "fixed_workflow" and rec.plan.path == "order_in"
    assert rec.cost_usd == 0.0 and rec.tokens_in == 0
    assert score_run(rec, SCEN["S-01"]).passed


def test_fixed_workflow_cannot_choose_to_cook_or_fall_back():
    cook = score_run(run_strategy("fixed_workflow", SCEN["S-02"]), SCEN["S-02"])
    assert cook.checks["path"] is False and "path" in cook.failed  # always orders in
    blocked = run_strategy("fixed_workflow", SCEN["S-04"])
    assert blocked.stop_reason == "no_option" and blocked.plan is None
    assert "trajectory" in score_run(blocked, SCEN["S-04"]).failed  # never looked at Instamart


def test_fixed_workflow_respects_the_new_budget_after_a_change():
    rec = run_strategy("fixed_workflow", SCEN["S-06"])
    assert rec.plan is not None and rec.plan.item_total <= 150


def test_fixed_workflow_stays_within_one_action_per_turn():
    llm = FixedWorkflowLLM()
    view = {
        "hard_constraints": {"vegetarian": False, "budget_inr": None},
        "untrusted_data": {"tool_results": []},
        "rejected_plans": [],
    }
    assert llm.next_action(view)["args"]["name"] == "search_restaurants"


# ---------------------------------------------------------------- one_shot


def test_one_shot_counts_invented_ids_as_hallucinations():
    llm = FakeLLM(
        [
            act(
                "propose_plan",
                path="order_in",
                reason="Biryani!",
                restaurant_id="999",
                items=[{"id": "555", "qty": 1}],
            )
        ]
    )
    rec = run_one_shot(SCEN["S-01"], llm)
    assert (
        rec.strategy == "one_shot" and rec.plan is None and rec.hallucinated == 2
    )  # dish + restaurant
    score = score_run(rec, SCEN["S-01"])
    assert score.checks["no_hallucinated_entities"] is False and score.checks["outcome"] is False
    assert score.metrics["hallucinated"] == 2


def test_one_shot_that_happens_to_name_real_entities_gets_a_plan():
    from evals.run import _truth_ledger

    truth = _truth_ledger(SCEN["S-01"])
    rid = next(iter(truth.restaurants))
    dish = next(m for m in truth.menu_items.values() if m.restaurant_id == rid and m.veg == "veg")
    llm = FakeLLM(
        [
            act(
                "propose_plan",
                path="order_in",
                reason="r",
                restaurant_id=rid,
                items=[{"id": dish.id, "qty": 1}],
            )
        ]
    )
    rec = run_one_shot(SCEN["S-01"], llm)
    assert rec.hallucinated == 0 and rec.plan is not None and rec.plan.items[0].name == dish.name


def test_one_shot_never_gets_tools_or_questions_and_a_non_plan_is_a_failed_run():
    llm = FakeLLM([act("ask_user", question="What do you like?", field="preference")])
    rec = run_one_shot(SCEN["S-01"], llm)
    view = llm.views[0]
    assert view["available_tools"] == {} and view["limits_left"]["questions"] == 0
    assert rec.plan is None and rec.stop_reason == "no_plan" and "not_a_plan" in rec.notes
    assert rec.tool_calls == 0 and rec.questions_asked == 0


def test_a_model_error_in_one_shot_is_a_failed_run_not_a_crash():
    class Broken:
        model = "broken"

        def next_action(self, view):
            raise RuntimeError("boom")

    rec = run_one_shot(SCEN["S-01"], Broken())
    assert rec.plan is None and any(n.startswith("model_error") for n in rec.notes)


# ---------------------------------------------------------------- registry


def test_strategy_names_and_errors():
    assert STRATEGIES == ("one_shot", "fixed_workflow", "agent")
    with pytest.raises(ValueError):
        run_strategy("agent", SCEN["S-01"])  # needs a model client
    with pytest.raises(ValueError):
        run_strategy("telepathy", SCEN["S-01"], DemoLLM())
