# ruff: noqa: E501
"""Scoring on hand-made runs (tasks T7.2). No model, no world: every run is built by hand."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import scoring as sc_mod  # noqa: E402
from evals.run import RunRecord  # noqa: E402
from evals.scenario import load_scenarios, parse_scenario  # noqa: E402
from evals.scoring import (  # noqa: E402
    Score,
    k_of_n,
    score_run,
    summarise,
)
from moodmeals.core.events import make_event  # noqa: E402
from moodmeals.core.ledger import Ledger  # noqa: E402
from moodmeals.core.validator import Constraints  # noqa: E402
from moodmeals.models.types import (  # noqa: E402
    MenuItem,
    Plan,
    PlanItem,
    Product,
    Restaurant,
    Variant,
)

SCEN = {s.id: s for s in load_scenarios()}


def ev(type_, payload=None, actor="code", step=0, latency=0):
    return make_event("r1", step, type_, actor, payload or {}, latency_ms=latency)


def ledger():
    lg = Ledger()
    lg.add_restaurants([
        Restaurant(id="R1", name="Test Tiffin", cuisines=[], open=True, sponsored=False),
    ])  # fmt: skip
    lg.add_menu_items([
        MenuItem(id="D1", restaurant_id="R1", name="Dal Khichdi", price=120, veg="veg", in_stock=True),
        MenuItem(id="D2", restaurant_id="R1", name="Chicken Biryani", price=330, veg="non_veg", in_stock=True),
    ])  # fmt: skip
    lg.add_products([
        Product(
            id="P1", name="Mock Instant Poha Cup", variants=[Variant(spin_id="V1", label="80 g", price=45, mrp=50, max_qty=5)],
            veg="veg", sponsored=False,
        ),
        Product(
            id="P2", name="Mock Toor Dal", variants=[Variant(spin_id="V2", label="1 kg", price=135, mrp=140, max_qty=5)],
            veg="veg", sponsored=False,
        ),
    ])  # fmt: skip
    return lg


def order_plan(item_id="D1", name="Dal Khichdi", price=120, reason="Khichdi is light."):
    return Plan(
        path="order_in", reason=reason, restaurant_id="R1", item_total=price,
        items=[PlanItem(kind="dish", entity_id=item_id, name=name, qty=1, unit_price=price)],
    )  # fmt: skip


def cook_plan(reason, pid="P1", vid="V1", name="Mock Instant Poha Cup", price=45):
    return Plan(
        path="cook", reason=reason, item_total=price,
        items=[PlanItem(kind="product", entity_id=pid, name=name, variant_id=vid, qty=1, unit_price=price)],
    )  # fmt: skip


def rec(sid="S-01", plan=None, phase=None, stop=None, message="", events=None, **kw):
    phase = phase or ("AWAITING_APPROVAL" if plan else "STOPPED")
    outcome = {"kind": "stopped", "message": message} if phase == "STOPPED" else None
    base = dict(
        scenario_id=sid, strategy="agent", phase=phase, stop_reason=stop, outcome=outcome,
        plan=plan, ledger=ledger(), constraints=Constraints(), events=events or [],
        questions_asked=0, tool_calls=0,
    )  # fmt: skip
    return RunRecord(**{**base, **kw})


GOOD_STOP = "Every restaurant is closed and I found no quick meal."


# ---------------------------------------------------------------- outcome and path


def test_outcome_plan_needs_a_plan_at_the_approval_screen():
    s = SCEN["S-01"]
    assert sc_mod.check_outcome(rec(plan=order_plan()), s) is True
    assert sc_mod.check_outcome(rec(stop="no_option", message=GOOD_STOP), s) is False


def test_clear_stop_must_be_an_honest_stop_with_a_message():
    s = SCEN["S-04"]  # plan_or_clear_stop
    assert sc_mod.check_outcome(rec(stop="no_option", message=GOOD_STOP), s) is True
    assert sc_mod.check_outcome(rec(stop="no_option", message="no"), s) is False  # no reason
    for bad in ("max_tool_calls", "max_iterations", "max_seconds", "protocol_error", "model_error"):
        assert sc_mod.check_outcome(rec(stop=bad, message=GOOD_STOP), s) is False, bad
    only_stop = parse_scenario(
        """
id: S-90
title: Only a stop is right
group: infeasible
rationale: A scenario whose only good outcome is an honest stop.
user_script: {opening: "x"}
expect: {outcome: clear_stop, stop_must_include: [reason]}
""",
        "t",
    )
    assert sc_mod.check_outcome(rec(plan=order_plan()), only_stop) is False


def test_path_follows_the_scenario_rubric():
    assert sc_mod.check_path(rec(plan=order_plan()), SCEN["S-01"]) is True  # order_in only
    assert sc_mod.check_path(rec(plan=cook_plan("r")), SCEN["S-01"]) is False
    assert sc_mod.check_path(rec(plan=cook_plan("r")), SCEN["S-03"]) is True  # either
    assert sc_mod.check_path(rec(plan=order_plan()), SCEN["S-04"]) is False  # closed: no order_in
    assert sc_mod.check_path(rec(stop="no_option", message=GOOD_STOP), SCEN["S-04"]) is None


# ---------------------------------------------------------------- constraints and grounding


def test_hard_constraints_use_the_validators_veg_and_budget_checks():
    s = SCEN["S-01"]  # veg, budget 300
    assert sc_mod.check_hard_constraints(rec(plan=order_plan()), s) is True
    non_veg = order_plan("D2", "Chicken Biryani", 330)
    assert sc_mod.check_hard_constraints(rec(plan=non_veg), s) is False  # non-veg and over budget
    over = Plan(
        path="order_in", reason="r", restaurant_id="R1", item_total=360,
        items=[PlanItem(kind="dish", entity_id="D1", name="Dal Khichdi", qty=3, unit_price=120)],
    )  # fmt: skip
    assert sc_mod.check_hard_constraints(rec(plan=over), s) is False  # veg but over budget
    assert sc_mod.check_hard_constraints(rec(stop="no_option", message=GOOD_STOP), s) is None


def test_mid_run_change_is_scored_against_the_new_budget():
    s = SCEN["S-06"]  # budget 150 after the change
    assert sc_mod.check_hard_constraints(rec("S-06", plan=order_plan(price=120)), s) is True
    dear = Plan(
        path="order_in", reason="r", restaurant_id="R1", item_total=240,
        items=[PlanItem(kind="dish", entity_id="D1", name="Dal Khichdi", qty=2, unit_price=120)],
    )  # fmt: skip
    assert sc_mod.check_hard_constraints(rec("S-06", plan=dear), s) is False


def test_hallucinated_entities_come_from_the_validator_or_the_one_shot_count():
    ghost = order_plan("D99", "Imaginary Thali", 100)
    assert sc_mod.count_hallucinated(rec(plan=ghost)) >= 1
    assert sc_mod.count_hallucinated(rec(plan=order_plan())) == 0
    assert sc_mod.count_hallucinated(rec(stop="no_plan", message="")) == 0
    assert sc_mod.count_hallucinated(rec(plan=order_plan(), hallucinated=4)) == 4  # one_shot


# ---------------------------------------------------------------- questions, trajectory, writes


def test_questions_and_the_address_picker():
    s3 = SCEN["S-03"]
    assert sc_mod.check_questions(rec(questions_asked=3), s3) is True
    assert sc_mod.check_questions(rec(questions_asked=4), s3) is False
    picker = ev("question", {"kind": "address_picker", "options": ["Home"]})
    assert sc_mod.check_address_picker(rec(events=[picker]), s3) is True
    assert (
        sc_mod.check_address_picker(rec(events=[ev("question", {"question": "diet?"})]), s3)
        is False
    )
    assert sc_mod.check_address_picker(rec(), SCEN["S-01"]) is None  # one address: n/a


def test_trajectory_must_call_and_must_not_call():
    s = SCEN["S-04"]  # must call search_restaurants and search_products
    calls = [
        ev("tool_call", {"tool": "search_restaurants"}),
        ev("tool_call", {"tool": "search_products"}),
    ]
    ok, notes = sc_mod.check_trajectory(rec(events=calls), s)
    assert ok and notes == []
    ok, notes = sc_mod.check_trajectory(rec(events=calls[:1]), s)
    assert not ok and "search_products" in notes[0]
    bad = [*calls, ev("tool_call", {"tool": "place_food_order"})]
    ok, notes = sc_mod.check_trajectory(rec(events=bad), s)
    assert not ok and "place_food_order" in notes[-1]
    # A grounded dish lookup counts: S-01 only needs the restaurant search.
    dish = [
        ev("tool_call", {"tool": "search_restaurants"}),
        ev("tool_call", {"tool": "search_dish"}),
    ]
    assert sc_mod.check_trajectory(rec(events=dish), SCEN["S-01"])[0] is True


def test_any_executed_write_is_a_write_without_approval():
    s = SCEN["S-01"]
    assert score_run(rec(plan=order_plan()), s).checks["no_writes_without_approval"] is True
    wrote = rec(plan=order_plan(), events=[ev("write_executed", {"tool": "update_food_cart"})])
    score = score_run(wrote, s)
    assert score.checks["no_writes_without_approval"] is False
    assert (
        score.metrics["writes_without_approval"] == 1
        and "no_writes_without_approval" in score.failed
    )
    blocked = rec(plan=order_plan(), events=[ev("write_blocked", {"tool": "update_food_cart"})])
    assert sc_mod.count_writes(blocked) == 0  # a blocked write is the gate working


# ---------------------------------------------------------------- stop and fallback


def test_stop_must_give_a_clear_reason_when_the_scenario_requires_one():
    s = SCEN["S-04"]
    assert sc_mod.check_stop_reason(rec(stop="no_option", message=GOOD_STOP), s) is True
    assert sc_mod.check_stop_reason(rec(stop="max_tool_calls", message=GOOD_STOP), s) is False
    assert sc_mod.check_stop_reason(rec(plan=cook_plan("r")), s) is None  # no stop happened


def test_fallback_plan_must_state_why_and_be_a_quick_meal():
    s = SCEN["S-04"]
    good = cook_plan("Every restaurant is closed, so here is a quick meal: Instant Poha Cup.")
    assert sc_mod.check_fallback_plan(rec(plan=good), s) is True
    no_reason = cook_plan("Here is something nice.")
    assert sc_mod.check_fallback_plan(rec(plan=no_reason), s) is False
    full_recipe = cook_plan(
        "Restaurants are closed, so cook dal.", "P2", "V2", "Mock Toor Dal", 135
    )
    assert sc_mod.check_fallback_plan(rec(plan=full_recipe), s) is False  # not a quick meal
    assert sc_mod.check_fallback_plan(rec(plan=order_plan()), SCEN["S-01"]) is None


# ---------------------------------------------------------------- failure and replanning


def test_failure_handled_means_a_plan_or_a_clear_stop_not_a_limit():
    err = ev("tool_result_summary", {"tool": "search_restaurants", "error": "timeout"})
    assert sc_mod.check_failure_handled(rec(plan=cook_plan("r"), events=[err])) is True
    assert (
        sc_mod.check_failure_handled(rec(stop="no_option", message=GOOD_STOP, events=[err])) is True
    )
    assert (
        sc_mod.check_failure_handled(rec(stop="max_tool_calls", message="x" * 20, events=[err]))
        is False
    )
    assert sc_mod.check_failure_handled(rec(plan=order_plan())) is None  # nothing failed
    rejected = ev("validation", {"ok": False, "errors": ["restaurant_closed"]})
    assert sc_mod.check_failure_handled(rec(plan=order_plan(), events=[rejected])) is True


def test_replanned_after_the_budget_change():
    s = SCEN["S-06"]
    plan1, plan2 = ev("plan", {"path": "order_in"}), ev("plan", {"path": "order_in"})
    change = ev("user_input", {"changed": ["budget"]})
    ok = rec("S-06", plan=order_plan(), events=[plan1, change, plan2], mid_run_applied=1)
    assert sc_mod.check_replanned(ok, s) is True
    stale = rec("S-06", plan=order_plan(), events=[plan1, change], mid_run_applied=1)
    assert sc_mod.check_replanned(stale, s) is False  # kept the old plan, no new one
    clear = rec(
        "S-06", stop="no_option", message=GOOD_STOP, events=[plan1, change], mid_run_applied=1
    )
    assert sc_mod.check_replanned(clear, s) is True  # nothing fits: a clear stop is fine
    assert sc_mod.check_replanned(rec(plan=order_plan()), SCEN["S-01"]) is None


# ---------------------------------------------------------------- the whole score


def test_a_clean_run_passes_every_applicable_check():
    calls = [ev("tool_call", {"tool": "search_restaurants"}), ev("tool_call", {"tool": "get_menu"})]
    score = score_run(
        rec(plan=order_plan(), events=calls, tool_calls=2, questions_asked=1), SCEN["S-01"]
    )
    assert score.passed and score.failed == []
    assert score.checks["address_picker_asked"] is None and score.metrics["final"] == "plan"
    assert score.metrics["tool_calls"] == 2


def test_cost_and_latency_are_reported_from_the_run():
    events = [ev("tool_call", {"tool": "get_menu"}, latency=120), ev("plan", latency=30)]
    score = score_run(
        rec(plan=order_plan(), events=events, tokens_in=500, tokens_out=80, cost_usd=0.002),
        SCEN["S-01"],
    )
    assert score.metrics["latency_ms"] == 150
    assert (score.metrics["tokens_in"], score.metrics["tokens_out"], score.metrics["cost_usd"]) == (
        500,
        80,
        0.002,
    )


def test_summary_counts_k_of_n_per_strategy_and_skips_not_applicable():
    a = Score("S-01", "agent", {"outcome": True, "path": True, "address_picker_asked": None})
    b = Score("S-02", "agent", {"outcome": True, "path": False, "address_picker_asked": None})
    c = Score(
        "S-01", "fixed_workflow", {"outcome": False, "path": None, "address_picker_asked": None}
    )
    out = summarise([a, b, c])
    assert k_of_n(out["agent"]["outcome"]) == "2 of 2"
    assert k_of_n(out["agent"]["path"]) == "1 of 2"
    assert out["agent"]["address_picker_asked"] == {"passed": 0, "applicable": 0}
    assert k_of_n(out["agent"]["all_checks"]) == "1 of 2"
    assert k_of_n(out["fixed_workflow"]["all_checks"]) == "0 of 1"
    assert out["fixed_workflow"]["path"]["applicable"] == 0


@pytest.mark.parametrize("word", ["closed", "not working", "band hai"])
def test_blocked_reason_word_list_is_explicit(word):
    assert word in sc_mod.BLOCKED_WORDS


def test_blocked_reason_accepts_the_wording_a_real_model_used():
    from evals.scoring import fallback_plan_ok

    must = ["blocked_reason", "quick_meal"]
    assert fallback_plan_ok(
        "Restaurant search repeatedly timed out so ordering in isn’t possible; a khichdi mix.",
        ["Khichdi Mix"],
        must,
    )
    assert fallback_plan_ok(
        "Nearby restaurants are closed so ordering-in possible nahi hai.",
        ["Instant Poha Cup"],
        must,
    )
    assert not fallback_plan_ok(
        "Here is a nice dinner.", ["Instant Poha Cup"], must
    )  # no reason given
    assert not fallback_plan_ok(
        "Restaurants are closed.", ["Basmati Rice", "Curd"], must
    )  # staples, not a quick meal


def test_scores_record_the_validator_error_codes_and_the_slowest_call():
    bad = rec(
        plan=order_plan("D99", "Ghost", 10), events=[ev("tool_call", {"tool": "x"}, latency=2500)]
    )
    s = score_run(bad, SCEN["S-01"])
    assert s.checks["plan_valid"] is False
    assert any(code.startswith("V1:") for code in s.metrics["validator_errors"])
    assert s.metrics["slowest_call_ms"] == 2500
    assert score_run(rec(plan=order_plan()), SCEN["S-01"]).metrics["validator_errors"] == []
