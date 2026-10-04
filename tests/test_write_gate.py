import pytest

from moodmeals.providers.gate import WriteAction, WriteGate, params_hash

ACTION = WriteAction("update_food_cart", {"items": [{"id": "m1", "qty": 1}], "restaurant_id": "r1"})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(mode="live", ttl=300.0, fail=False):
    calls = []

    def executor(tool, params):
        calls.append((tool, params))
        if fail:
            raise RuntimeError("boom")
        return {"ok": True}

    clock = Clock()
    return WriteGate(mode, executor, ttl_s=ttl, clock=clock), calls, clock


def test_live_with_matching_approval_executes_once():
    gate, calls, _ = make("live")
    out = gate.execute(ACTION, gate.issue_approval(ACTION))
    assert (out.status, out.reason) == ("executed", "ok")
    assert len(calls) == 1


def test_no_approval_is_blocked():
    gate, calls, _ = make("live")
    out = gate.execute(ACTION, None)
    assert (out.status, out.reason) == ("blocked", "no_approval")
    assert calls == []


def test_approve_a_execute_b_is_blocked():
    gate, calls, _ = make("live")
    approval = gate.issue_approval(ACTION)
    other = WriteAction(
        "update_food_cart", {"items": [{"id": "m1", "qty": 5}], "restaurant_id": "r1"}
    )
    out = gate.execute(other, approval)
    assert (out.status, out.reason) == ("blocked", "params_mismatch")
    assert calls == []


def test_approval_cannot_be_reused():
    gate, calls, _ = make("live")
    approval = gate.issue_approval(ACTION)
    assert gate.execute(ACTION, approval).status == "executed"
    again = gate.execute(ACTION, approval)
    assert (again.status, again.reason) == ("blocked", "already_used")
    assert len(calls) == 1


def test_approval_expires():
    gate, calls, clock = make("live", ttl=60)
    approval = gate.issue_approval(ACTION)
    clock.t += 61
    out = gate.execute(ACTION, approval)
    assert (out.status, out.reason) == ("blocked", "expired")
    assert calls == []


def test_dry_run_never_calls_provider_even_with_approval():
    gate, calls, _ = make("dry_run")
    out = gate.execute(ACTION, gate.issue_approval(ACTION))
    assert out.status == "dry_run_preview"
    assert out.result["would_do"] == "update_food_cart"
    assert calls == []


def test_mock_mode_needs_approval_then_simulates():
    gate, calls, _ = make("mock")
    assert gate.execute(ACTION, None).status == "blocked"
    assert gate.execute(ACTION, gate.issue_approval(ACTION)).status == "simulated"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "tool",
    [
        "get_payment_options",
        "check_payment_status",
        "confirm_order",
        "create_address",
        "delete_address",
        "apply_food_coupon",
    ],
)
@pytest.mark.parametrize("mode", ["mock", "dry_run", "live"])
def test_forbidden_tools_are_always_blocked(tool, mode):
    gate, calls, _ = make(mode)
    action = WriteAction(tool, {})
    out = gate.execute(action, gate.issue_approval(action))
    assert (out.status, out.reason) == ("blocked", "forbidden_tool")
    assert calls == []


def test_unknown_tool_fails_closed():
    gate, calls, _ = make("live")
    action = WriteAction("search_restaurants", {"query": "x"})
    out = gate.execute(action, gate.issue_approval(action))
    assert (out.status, out.reason) == ("blocked", "unknown_tool")
    assert calls == []


def test_failed_write_is_not_retried_and_approval_is_spent():
    gate, calls, _ = make("live", fail=True)
    approval = gate.issue_approval(ACTION)
    assert gate.execute(ACTION, approval).status == "failed"
    assert gate.execute(ACTION, approval).reason == "already_used"
    assert len(calls) == 1


def test_every_outcome_is_logged():
    gate, _, _ = make("live")
    gate.execute(ACTION, None)
    approval = gate.issue_approval(ACTION)
    gate.execute(ACTION, approval)
    gate.execute(ACTION, approval)
    assert [e["type"] for e in gate.log] == [
        "write_blocked",
        "approval",
        "write_executed",
        "write_blocked",
    ]


def test_log_never_contains_param_values():
    gate, _, _ = make("live")
    secret = WriteAction("update_food_cart", {"note": "call 9876543210"})
    gate.execute(secret, gate.issue_approval(secret))
    assert "9876543210" not in str(gate.log)


def test_hash_is_order_independent_and_value_sensitive():
    assert params_hash("t", {"a": 1, "b": 2}) == params_hash("t", {"b": 2, "a": 1})
    assert params_hash("t", {"a": 1}) != params_hash("t", {"a": "1"})
    assert params_hash("t", {"a": 1}) != params_hash("u", {"a": 1})
