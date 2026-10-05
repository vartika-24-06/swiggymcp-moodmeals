"""Live-mode guard rails against a FAKE MCP connection (tasks T6.3).

No network and no sign-in: the fake speaks Swiggy's tool names over the synthetic mock world,
records every call, and holds synthetic carts. These tests prove the guards: no write without
approval, no silent overwrite of an existing cart, no order or payment tool, no cart contents
leaking out of the provider.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from moodmeals.core.loop import Agent
from moodmeals.models.fake import FakeLLM
from moodmeals.providers import world as w
from moodmeals.providers.gate import WriteAction
from moodmeals.providers.mcp_connection import CallTimeout
from moodmeals.providers.swiggy import SwiggyProvider
from moodmeals.providers.switches import Switches

SW = Switches()
RID, DISH = "61093", "61093009"
PRODUCT, VARIANT = "600005", "700050"
WRITE_TOOLS = {"update_cart", "update_food_cart", "place_food_order", "checkout"}
# Synthetic stand-ins for what a real cart reply can carry.
CART_ITEM = {"spinId": "SYN1", "itemName": "Zzyzx Marmalade", "quantity": 1}
CART_PII = {"selectedAddressDetails": {"address": "7 Synthetic Lane", "mobile": "0000000000"}}


class LiveFake:
    def __init__(self, im_items=(), food_items=(), n_addresses=1):
        self.world = w.build_world(1, n_addresses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.im_items, self.food_items = list(im_items), list(food_items)
        self.variant_dishes: set[str] = set()
        self.fail: dict[str, Exception] = {}  # raw tool name -> error to raise
        self.cart_reply: dict[str, Any] | None = None  # override a cart reply's shape

    @property
    def writes(self) -> list[tuple[str, str, dict[str, Any]]]:
        return [c for c in self.calls if c[1] in WRITE_TOOLS]

    def call(self, server: str, tool: str, args: dict[str, Any], timeout: float):
        self.calls.append((server, tool, args))
        if tool in self.fail:
            raise self.fail[tool]
        wd = self.world
        if tool == "get_addresses":
            return w.addresses_payload(wd, args["page"], args["pageSize"])
        if tool == "search_restaurants":
            return w.search_restaurants_payload(wd, SW, args["query"], args.get("offset", 0))
        if tool == "get_restaurant_menu":
            payload = w.menu_payload(
                wd, SW, args["restaurantId"], args.get("page", 1), args.get("pageSize", 5)
            )
            for cat in payload.get("categories", []):
                for item in cat.get("items", []):
                    if item["id"] in self.variant_dishes:
                        item["hasVariants"] = True
            return payload
        if tool == "search_products":
            return w.search_products_payload(wd, SW, args["query"], args.get("offset", 0))
        if tool in ("get_cart", "get_food_cart"):
            if self.cart_reply is not None:
                return self.cart_reply
            items = self.im_items if tool == "get_cart" else self.food_items
            return {"items": list(items), "cartTotalAmount": "₹1", **CART_PII}
        if tool == "update_cart":
            self.im_items = [
                {"spinId": i["spinId"], "quantity": i["quantity"]} for i in args["items"]
            ]
            return {"billBreakdown": "secret bill", **CART_PII}
        if tool == "update_food_cart":
            self.food_items = [{"menu_item_id": i["menu_item_id"]} for i in args["cartItems"]]
            return {"billBreakdown": "secret bill", **CART_PII}
        raise AssertionError(f"unexpected Swiggy tool {tool}")


def act(kind: str, **args) -> dict:
    return {"action": kind, "args": args, "rationale": "t"}


SEARCH = act("tool_call", name="search_restaurants", params={"query": "biryani"})
MENU = act("tool_call", name="get_menu", params={"restaurant_id": RID})
ORDER = act(
    "propose_plan", path="order_in", reason="r", restaurant_id=RID, items=[{"id": DISH, "qty": 1}]
)
COOK = [
    act("tool_call", name="search_products", params={"query": "paneer"}),
    act(
        "propose_plan",
        path="cook",
        reason="r",
        items=[{"id": PRODUCT, "variant_id": VARIANT, "qty": 1}],
    ),
]


def live_run(conn: LiveFake, script: list, mode: str = "live"):
    llm = FakeLLM(script)
    agent = Agent(llm, SwiggyProvider(conn, mode))
    state = agent.start("hungry")
    agent.run(state)
    return agent, state, llm


# --------------------------------------------------------------------------- provider


def test_cart_updates_exist_only_in_live_mode():
    conn = LiveFake()
    p = SwiggyProvider(conn, "dry_run")
    params = {"address_id": "a1", "restaurant_id": RID, "items": [{"id": DISH, "qty": 1}]}
    assert p.call("update_food_cart", params).error.kind == "unknown_tool"
    assert p.call("update_cart", {"address_id": "a1", "items": []}).error.kind == "unknown_tool"
    assert conn.calls == []


def test_live_provider_translates_cart_updates_and_drops_the_reply():
    conn = LiveFake()
    p = SwiggyProvider(conn, "live")
    r = p.call("update_cart", {"address_id": "a1", "items": [{"spin_id": "S1", "qty": 2}]})
    assert r.ok and r.data == {"updated": True}  # the reply (bill, address) never comes back
    assert conn.calls[-1] == (
        "im",
        "update_cart",
        {"selectedAddressId": "a1", "items": [{"spinId": "S1", "quantity": 2}]},
    )
    r = p.call(
        "update_food_cart",
        {"address_id": "a1", "restaurant_id": RID, "items": [{"id": DISH, "qty": 1}]},
    )
    assert r.ok and r.data == {"updated": True}
    assert conn.calls[-1] == (
        "food",
        "update_food_cart",
        {
            "restaurantId": RID,
            "addressId": "a1",
            "cartItems": [{"menu_item_id": DISH, "quantity": 1}],
        },
    )


@pytest.mark.parametrize(
    "tool",
    [
        "place_food_order", "checkout", "get_payment_options", "check_payment_status",
        "confirm_order", "create_address", "delete_address", "flush_food_cart", "clear_cart",
        "apply_food_coupon",
    ],
)  # fmt: skip
def test_live_provider_never_places_orders_or_pays(tool):
    conn = LiveFake()
    r = SwiggyProvider(conn, "live").call(tool, {"address_id": "a1", "items": [{"id": "x"}]})
    assert not r.ok and r.error.kind == "unknown_tool"
    assert conn.calls == []


@pytest.mark.parametrize(
    "tool, params",
    [
        ("update_cart", {"items": [{"spin_id": "S", "qty": 1}]}),  # no address
        ("update_cart", {"address_id": "a1", "items": []}),
        ("update_cart", {"address_id": "a1", "items": [{"spin_id": "S", "qty": 0}]}),
        ("update_cart", {"address_id": "a1", "items": [{"spin_id": "S", "qty": 99}]}),
        ("update_cart", {"address_id": "a1", "items": [{"qty": 1}]}),  # no spin id
        ("update_food_cart", {"address_id": "a1", "items": [{"id": "d", "qty": 1}]}),  # no rest.
    ],
)
def test_live_write_params_are_checked_before_the_network(tool, params):
    conn = LiveFake()
    assert SwiggyProvider(conn, "live").call(tool, params).error.kind == "bad_params"
    assert conn.calls == []


def test_cart_state_returns_only_emptiness():
    conn = LiveFake(im_items=[CART_ITEM])
    p = SwiggyProvider(conn, "dry_run")  # a read: allowed in every mode
    r = p.call("get_cart_state", {"cart": "im"})
    assert r.ok and r.data == {"empty": False}
    assert "Zzyzx" not in json.dumps(r.data) and "Synthetic Lane" not in json.dumps(r.data)
    conn.im_items = []
    assert p.call("get_cart_state", {"cart": "im"}).data == {"empty": True}
    r = p.call("get_cart_state", {"cart": "food", "address_id": "a1"})
    assert r.data == {"empty": True}
    assert conn.calls[-1] == ("food", "get_food_cart", {"addressId": "a1"})


def test_cart_state_never_guesses():
    conn = LiveFake()
    p = SwiggyProvider(conn, "live")
    conn.cart_reply = {"message": "something new"}  # an unrecognised shape is not "empty"
    assert p.call("get_cart_state", {"cart": "im"}).error.kind == "error"
    conn.cart_reply = None
    conn.fail["get_cart"] = CallTimeout("get_cart")
    assert p.call("get_cart_state", {"cart": "im"}).error.kind == "timeout"
    assert p.call("get_cart_state", {"cart": "food"}).error.kind == "bad_params"  # no address
    assert p.call("get_cart_state", {"cart": "other"}).error.kind == "bad_params"


# --------------------------------------------------------------------------- the loop


def test_empty_instamart_cart_one_write_then_the_run_ends_without_an_order():
    conn = LiveFake()
    agent, state, _ = live_run(conn, COOK)
    assert state.phase == "AWAITING_APPROVAL" and state.cart_check == "empty"
    assert conn.writes == []  # nothing is written before approval
    assert not any(c[1] == "get_food_cart" for c in conn.calls)  # cook path: Instamart only
    agent.approve(state)
    assert [c[1] for c in conn.writes] == ["update_cart"]
    assert conn.writes[0][2]["items"] == [{"spinId": VARIANT, "quantity": 1}]
    assert state.phase == "DONE" and state.outcome["kind"] == "cart_updated"
    assert "Swiggy app" in state.outcome["message"]
    assert not any(c[1] in ("checkout", "place_food_order") for c in conn.calls)


def test_existing_instamart_cart_blocks_a_silent_overwrite():
    conn = LiveFake(im_items=[CART_ITEM])
    agent, state, _ = live_run(conn, COOK)
    assert state.cart_check == "not_empty" and state.phase == "AWAITING_APPROVAL"
    agent.approve(state)  # approving alone is not enough
    assert conn.writes == []
    assert state.phase == "AWAITING_APPROVAL"
    blocked = [e for e in state.events if e.type == "write_blocked"]
    assert blocked and blocked[-1].payload["reason"] == "cart_not_empty"
    assert not any(e.type == "approval" and e.payload.get("decision") == "approved"
                   for e in state.events)  # fmt: skip
    agent.confirm_replace(state)
    agent.approve(state)
    assert [c[1] for c in conn.writes] == ["update_cart"]
    assert state.outcome["kind"] == "cart_updated"


def test_confirm_replace_is_refused_when_there_is_nothing_to_confirm():
    agent, state, _ = live_run(LiveFake(), COOK)
    with pytest.raises(ValueError):
        agent.confirm_replace(state)


def test_a_cart_that_changed_after_the_screen_still_blocks():
    conn = LiveFake()
    agent, state, _ = live_run(conn, COOK)
    assert state.cart_check == "empty"
    conn.im_items = [CART_ITEM]  # the person added something in the Swiggy app meanwhile
    agent.approve(state)
    assert conn.writes == [] and state.cart_check == "not_empty"
    assert state.phase == "AWAITING_APPROVAL"


def test_an_unreadable_cart_stops_the_run_without_writing():
    conn = LiveFake()
    conn.fail["get_cart"] = RuntimeError("boom")
    agent, state, _ = live_run(conn, COOK)
    assert state.cart_check == "unknown"
    agent.approve(state)
    assert conn.writes == [] and state.phase == "STOPPED"
    assert state.stop_reason == "cart_unverified"


def test_food_plan_checks_the_food_cart_only():
    conn = LiveFake(food_items=[{"menu_item_id": "other"}])
    agent, state, _ = live_run(conn, [SEARCH, MENU, ORDER])
    assert state.plan.path == "order_in" and state.cart_check == "not_empty"
    reads = [c for c in conn.calls if c[1] in ("get_cart", "get_food_cart")]
    assert [c[1] for c in reads] == ["get_food_cart"]  # not the Instamart cart
    assert reads[0][2].get("addressId")
    agent.approve(state)
    assert conn.writes == []
    agent.confirm_replace(state)
    agent.approve(state)
    assert [c[1] for c in conn.writes] == ["update_food_cart"]
    assert conn.writes[0][2]["restaurantId"] == RID
    assert conn.writes[0][2]["cartItems"] == [{"menu_item_id": DISH, "quantity": 1}]
    assert state.outcome["kind"] == "cart_updated"
    assert not any(c[1] == "place_food_order" for c in conn.calls)


def test_live_rejects_a_dish_with_variants_and_retries_without_writing():
    conn = LiveFake()
    conn.variant_dishes = {DISH}

    def second_pick(view):
        items = next(
            t["result"]["items"]
            for t in view["untrusted_data"]["tool_results"]
            if t["tool"] == "get_menu"
        )
        ok = next(i for i in items if i["in_stock"] and not i["variants"] and i["id"] != DISH)
        return act(
            "propose_plan", path="order_in", reason="r", restaurant_id=RID,
            items=[{"id": ok["id"], "qty": 1}],
        )  # fmt: skip

    agent, state, _ = live_run(conn, [SEARCH, MENU, ORDER, second_pick])
    assert state.phase == "AWAITING_APPROVAL" and state.plan.items[0].entity_id != DISH
    assert conn.writes == []


def test_no_write_without_a_matching_unused_approval():
    conn = LiveFake()
    agent, state, _ = live_run(conn, COOK)
    a = WriteAction("update_cart", {"items": [{"product_id": "p", "spin_id": "S1", "qty": 1}]})
    b = WriteAction("update_cart", {"items": [{"product_id": "p", "spin_id": "S2", "qty": 1}]})
    assert agent.gate.execute(a, None).reason == "no_approval"
    approval = agent.gate.issue_approval(a)
    assert agent.gate.execute(b, approval).reason == "params_mismatch"  # approve A, run B
    assert conn.writes == []
    assert agent.gate.execute(a, approval).status == "executed"
    assert agent.gate.execute(a, approval).reason == "already_used"  # single use
    assert len(conn.writes) == 1


def test_forbidden_and_order_tools_are_blocked_by_the_gate_in_live_mode():
    conn = LiveFake()
    agent, _, _ = live_run(conn, COOK)
    approval = agent.gate.issue_approval(WriteAction("get_payment_options", {}))
    assert agent.gate.execute(WriteAction("get_payment_options", {}), approval).reason == (
        "forbidden_tool"
    )
    # An order tool passes the gate's own checks, so the provider is what refuses it:
    r = agent.provider.call("place_food_order", {"address_id": "a1"})
    assert not r.ok and r.error.kind == "unknown_tool"
    assert conn.writes == []


def test_cart_contents_never_reach_the_model_or_the_state():
    conn = LiveFake(im_items=[CART_ITEM])
    agent, state, llm = live_run(conn, COOK)
    agent.confirm_replace(state)
    agent.approve(state)
    everything = json.dumps(llm.views) + state.model_dump_json()
    assert "Zzyzx" not in everything and "Synthetic Lane" not in everything
    assert "secret bill" not in everything and "0000000000" not in everything


def test_dry_run_never_reads_or_writes_a_cart():
    conn = LiveFake()
    agent, state, _ = live_run(conn, COOK, mode="dry_run")
    assert state.cart_check is None
    agent.approve(state)
    assert state.outcome["kind"] == "dry_run_preview"
    assert not any(c[1] in ("get_cart", "get_food_cart") for c in conn.calls)
    assert conn.writes == []


# --------------------------------------------------------------------------- approval screen


def _screen(conn, script):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
    import ui_text

    agent, state, _ = live_run(conn, script)
    return ui_text, agent, state


def test_approval_screen_warns_about_the_real_cart_and_unsafe_states():
    ui_text, agent, state = _screen(LiveFake(), COOK)
    a = ui_text.approval_text(state)
    assert "real Swiggy cart" in a["warning"] and "cancellable" in a["warning"]
    assert not a["needs_confirm"] and not a["blocked"]

    ui_text, agent, state = _screen(LiveFake(im_items=[CART_ITEM]), COOK)
    a = ui_text.approval_text(state)
    assert a["needs_confirm"] and "replace" in a["warning"] and "Instamart" in a["warning"]
    agent.confirm_replace(state)
    assert not ui_text.approval_text(state)["needs_confirm"]

    ui_text, _, state = _screen(LiveFake(food_items=[CART_ITEM]), [SEARCH, MENU, ORDER])
    a = ui_text.approval_text(state)
    assert a["needs_confirm"] and "Food" in a["warning"]

    conn = LiveFake()
    conn.fail["get_cart"] = RuntimeError("x")
    ui_text, _, state = _screen(conn, COOK)
    assert ui_text.approval_text(state)["blocked"]


# --------------------------------------------------------------------------- Food cart shapes

FOOD_EMPTY = {"statusCode": 0, "statusMessage": "CART", "data": None, "successful": True}


def test_food_empty_cart_reply_is_recognised_and_other_envelopes_are_not():
    conn = LiveFake()
    p = SwiggyProvider(conn, "live")
    args = {"cart": "food", "address_id": "a1"}
    conn.cart_reply = dict(FOOD_EMPTY)
    assert p.call("get_cart_state", args).data == {"empty": True}
    conn.cart_reply = {**FOOD_EMPTY, "data": {"cartItems": [{"menu_item_id": "d1"}]}}
    assert p.call("get_cart_state", args).data == {"empty": False}
    for odd in (
        {**FOOD_EMPTY, "successful": False},  # a failed call is not an empty cart
        {**FOOD_EMPTY, "statusCode": 1},
        {"statusCode": 0, "successful": True},  # no data key at all
        {**FOOD_EMPTY, "data": {"somethingNew": 1}},  # a shape not seen yet
    ):
        conn.cart_reply = odd
        assert p.call("get_cart_state", args).error.kind == "error", odd


def test_food_plan_with_the_real_empty_cart_reply_writes_once():
    conn = LiveFake()
    conn.cart_reply = dict(FOOD_EMPTY)
    agent, state, _ = live_run(conn, [SEARCH, MENU, ORDER])
    assert state.cart_check == "empty"
    agent.approve(state)
    assert [c[1] for c in conn.writes] == ["update_food_cart"]
    assert state.outcome["kind"] == "cart_updated"
