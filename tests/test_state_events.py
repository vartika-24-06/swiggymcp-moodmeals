import json

from moodmeals.core.events import make_event
from moodmeals.core.state import RunState
from moodmeals.models.types import Plan, PlanItem

ADDRESS = "Flat 12B, Sample Heights, Demo Nagar, Testville"


def test_event_payload_and_rationale_are_redacted_at_creation():
    e = make_event(
        "r1",
        0,
        "tool_call",
        "model",
        {"query": "khichdi", "deliver_to": f"{ADDRESS} 9876543210", "address": ADDRESS},
        rationale=f"user is at {ADDRESS}",
        known_sensitive=[ADDRESS],
    )
    blob = e.model_dump_json()
    assert "Sample Heights" not in blob and "9876543210" not in blob
    assert e.payload["query"] == "khichdi"


def test_state_round_trips_to_json():
    s = RunState(mode="dry_run")
    s.add_event("user_input", "user", {"text": "hungry"})
    s.plan = Plan(
        path="cook",
        reason="quick",
        item_total=60,
        items=[PlanItem(kind="product", entity_id="p1", name="Dal", qty=1, unit_price=60)],
    )
    assert RunState.from_json(s.to_json()) == s


def test_state_scrubs_registered_address_and_never_serialises_it():
    s = RunState()
    s.register_sensitive(ADDRESS)
    s.add_event("tool_call", "code", {"note": f"using {ADDRESS}"})
    blob = s.to_json()
    assert "Sample Heights" not in blob
    assert "_sensitive" not in json.loads(blob)
    # The set is private: a state loaded from JSON starts with no sensitive strings.
    assert RunState.from_json(blob)._sensitive == set()


def test_step_counter_increments_per_event():
    s = RunState()
    s.add_event("user_input", "user", {"text": "a"})
    s.add_event("question", "model", {"text": "b"})
    assert [e.step for e in s.events] == [0, 1]
    assert s.step == 2
