"""The eval scenario format and the smoke set (tasks T7.1). No model is called."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.scenario import (  # noqa: E402
    GROUPS,
    SCENARIO_DIR,
    ScenarioError,
    load_scenarios,
    parse_scenario,
)
from moodmeals.providers.mock import MockProvider  # noqa: E402
from moodmeals.tools.normalise import parse_restaurants  # noqa: E402

GOOD = """
id: S-99
title: A valid scenario for tests
group: happy_path
rationale: A rationale that is long enough to explain the expected path.
user_script: {opening: "hungry", answers: {diet: veg}}
expect: {outcome: plan, paths_acceptable: [order_in]}
"""


def with_edit(old: str, new: str) -> str:
    assert old in GOOD
    return GOOD.replace(old, new)


def test_all_committed_scenarios_load_with_unique_contiguous_ids():
    scenarios = load_scenarios()
    ids = [s.id for s in scenarios]
    assert ids == sorted(set(ids)) and len(ids) >= 6
    assert ids == [f"S-{i:02d}" for i in range(1, len(ids) + 1)]  # no gaps
    assert all(s.group in GROUPS for s in scenarios)


def test_smoke_set_is_the_six_planned_scenarios_across_groups():
    smoke = load_scenarios(smoke_only=True)
    assert len(smoke) == 6
    assert len({s.group for s in smoke}) >= 5  # breadth, not six happy paths
    assert {"happy_path", "infeasible", "tool_failure", "mid_run_change"} <= {
        s.group for s in smoke
    }


def test_every_scenario_explains_its_expected_path_and_never_approves_a_write():
    for s in load_scenarios():
        assert len(s.rationale) >= 20, s.id
        assert s.user_script.approve is False
        assert {"place_food_order", "checkout"} <= set(s.expect.must_not_call), s.id


def test_scenarios_hold_no_real_looking_data():
    for path in SCENARIO_DIR.glob("S-*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"\d{6,}", text), path.name  # phone-like digit runs
        assert "@" not in text and "http" not in text, path.name


def test_each_world_builds_and_creates_the_situation_it_describes():
    for s in load_scenarios():
        prov = MockProvider(
            seed=s.world.seed, switches=s.world.build_switches(), n_addresses=s.world.addresses
        )
        addresses = prov.call("list_addresses", {}).data["addresses"]
        assert len(addresses) == s.world.addresses, s.id


def test_world_switches_do_what_the_smoke_scenarios_say():
    by_id = {s.id: s for s in load_scenarios()}

    def provider(sid):
        w = by_id[sid].world
        return MockProvider(seed=w.seed, switches=w.build_switches(), n_addresses=w.addresses)

    closed = parse_restaurants(
        provider("S-04").call("search_restaurants", {"query": "biryani"}).data
    )
    assert closed and not any(r.open for r in closed)  # everything is closed
    r = provider("S-05").call("search_restaurants", {"query": "biryani"})
    assert not r.ok and r.error.kind == "timeout"
    assert provider("S-02").call("search_products", {"query": "dal"}).data["products"]
    assert by_id["S-03"].expect.address_picker and by_id["S-03"].world.addresses == 3


def test_blocked_order_scenarios_expect_a_quick_meal_offer_or_a_stop_with_a_reason():
    by_id = {s.id: s for s in load_scenarios()}
    for sid in ("S-04", "S-05"):
        e = by_id[sid].expect
        assert e.outcome == "plan_or_clear_stop" and e.paths_acceptable == ["cook"]
        assert set(e.plan_must_include) == {"blocked_reason", "quick_meal"}
        assert e.stop_must_include == ["reason"]
        assert "search_products" in e.must_call  # it must actually look at Instamart


def test_mid_run_change_scenario_lowers_the_budget():
    s = next(s for s in load_scenarios() if s.group == "mid_run_change")
    step = s.user_script.mid_run[0]
    assert step.do == "change_constraints" and step.with_ == {"budget": 150}
    assert s.expect.hard_constraints["budget"] == 150  # scored against the NEW budget


@pytest.mark.parametrize(
    "old, new, why",
    [
        ("group: happy_path", "group: vibes", "bad group"),
        ("id: S-99", "id: 99", "bad id"),
        ("title: A valid scenario for tests", "title: A valid scenario for tests\nextra: 1", "key"),
        ("answers: {diet: veg}", "answers: {mood: sad}", "unknown question field"),
        ("answers: {diet: veg}", "answers: {diet: veg}, approve: true", "approve a write"),
        ("paths_acceptable: [order_in]", "paths_acceptable: []", "plan needs a path"),
        ("outcome: plan,", "outcome: clear_stop,", "clear_stop with a path"),
        ("paths_acceptable: [order_in]", "paths_acceptable: [order_in], max_questions: 4", "4"),
        ("outcome: plan,", "outcome: clear_stop,", "a stop with no reason"),
        (
            "paths_acceptable: [order_in]",
            "paths_acceptable: [order_in], stop_must_include: [reason]",
            "plan",
        ),
        (
            "outcome: plan,",
            "outcome: plan_or_clear_stop, stop_must_include: [reason],"
            " plan_must_include: [quick_meal],",
            "quick meal must use the cook path only",
        ),
        (
            "paths_acceptable: [order_in]",
            "paths_acceptable: [order_in], address_picker: true",
            "pick",
        ),
        (
            "paths_acceptable: [order_in]",
            "paths_acceptable: [order_in], must_call: [bogus]",
            "tool",
        ),
        (
            "paths_acceptable: [order_in]",
            "paths_acceptable: [order_in], must_call: [get_menu], must_not_call: [get_menu]",
            "both",
        ),
        (
            "rationale: A rationale that is long enough to explain the expected path.",
            "rationale: short",
            "rationale",
        ),
    ],
)
def test_bad_scenarios_are_rejected(old, new, why):
    with pytest.raises(ScenarioError):
        parse_scenario(with_edit(old, new), "bad.yaml")


def test_world_and_script_values_are_checked():
    bad_world = [
        "world: {switches: {not_a_switch: true}}",
        "world: {switches: {fail_tools: {place_food_order: timeout}}}",  # only read tools fail
        "world: {switches: {fail_tools: {search_restaurants: explode}}}",
        "world: {addresses: 9}",
        "world: {addresses: 2}",  # two addresses but no picker expected
    ]
    for extra in bad_world:
        with pytest.raises(ScenarioError):
            parse_scenario(GOOD + extra + "\n", "bad.yaml")
    bad_script = [
        'user_script: {opening: "x", constraints: {budget: -5}}',
        'user_script: {opening: "x", address: 2}',
        'user_script: {opening: "x", mid_run: [{do: change_constraints}]}',
        'user_script: {opening: "x", mid_run: [{do: change_constraints, with: {colour: red}}]}',
        'user_script: {opening: "x", mid_run: [{do: another_idea, with: {budget: 1}}]}',
    ]
    for line in bad_script:
        text = GOOD.replace('user_script: {opening: "hungry", answers: {diet: veg}}', line)
        with pytest.raises(ScenarioError):
            parse_scenario(text, "bad.yaml")


def test_file_names_must_match_ids_and_ids_must_be_unique(tmp_path):
    (tmp_path / "S-99_ok.yaml").write_text(GOOD, encoding="utf-8")
    assert [s.id for s in load_scenarios(tmp_path)] == ["S-99"]
    (tmp_path / "S-98_wrong.yaml").write_text(GOOD, encoding="utf-8")  # holds id S-99
    with pytest.raises(ScenarioError):
        load_scenarios(tmp_path)
    (tmp_path / "S-98_wrong.yaml").unlink()
    (tmp_path / "S-99_copy.yaml").write_text(GOOD, encoding="utf-8")
    with pytest.raises(ScenarioError):
        load_scenarios(tmp_path)


def test_not_yaml_and_not_a_mapping_are_scenario_errors():
    for text in ("- just\n- a list\n", "id: [unclosed", ""):
        with pytest.raises(ScenarioError):
            parse_scenario(text, "bad.yaml")
