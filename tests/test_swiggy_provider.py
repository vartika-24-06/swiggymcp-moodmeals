"""SwiggyProvider over a fake connection backed by the mock world (tasks T6.1, T6.3 prep).

No network, no sign-in. The fake speaks Swiggy's tool names and parameter names, so these
tests prove the translation and the read-only allowlist.
"""

from __future__ import annotations

from typing import Any

import pytest

from moodmeals.core.loop import Agent
from moodmeals.models.fake import FakeLLM
from moodmeals.providers import world as w
from moodmeals.providers.mcp_connection import CallTimeout, extract_payload
from moodmeals.providers.swiggy import READ_ALLOWLIST, SwiggyProvider
from moodmeals.providers.switches import Switches

SW = Switches()


class FakeConnection:
    def __init__(self, n_addresses: int = 1, seed: int = 1):
        self.world = w.build_world(seed, n_addresses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.fail: Exception | None = None

    def call(self, server: str, tool: str, args: dict[str, Any], timeout: float):
        self.calls.append((server, tool, args))
        if self.fail:
            raise self.fail
        wd = self.world
        if tool == "get_addresses":
            return w.addresses_payload(wd, args["page"], args["pageSize"])
        if tool == "search_restaurants":
            return w.search_restaurants_payload(wd, SW, args["query"], args.get("offset", 0))
        if tool == "get_restaurant_menu":
            return w.menu_payload(
                wd, SW, args["restaurantId"], args.get("page", 1), args.get("pageSize", 5)
            )
        if tool == "search_menu":
            return w.dish_search_payload(
                wd, SW, args["query"], args["restaurantIdOfAddedItem"],
                bool(args["vegFilter"]), args.get("offset", 0),
            )  # fmt: skip
        if tool == "search_products":
            return w.search_products_payload(wd, SW, args["query"], args.get("offset", 0))
        raise AssertionError(f"unexpected Swiggy tool {tool}")


def provider(**kw):
    conn = FakeConnection(**kw)
    return SwiggyProvider(conn), conn


def test_translates_names_and_params():
    p, conn = provider()
    p.call("search_restaurants", {"query": "dal", "address_id": "addr_1"})
    p.call("get_menu", {"restaurant_id": "61093", "page_size": 20, "address_id": "addr_1"})
    p.call("search_dish", {"query": "rice", "restaurant_id": "61093", "veg_only": True})
    p.call("search_products", {"query": "dal", "address_id": "addr_1"})
    assert conn.calls[0] == (
        "food",
        "search_restaurants",
        {"query": "dal", "offset": 0, "addressId": "addr_1"},
    )
    assert conn.calls[1][2] == {
        "restaurantId": "61093",
        "addressId": "addr_1",
        "pageSize": 8,
    }  # capped
    assert (
        conn.calls[2][2]["vegFilter"] == 1
        and conn.calls[2][2]["restaurantIdOfAddedItem"] == "61093"
    )
    assert conn.calls[3][0] == "im"  # Instamart goes to the Instamart server


def test_results_parse_with_the_shared_normalisers():
    from moodmeals.tools.normalise import parse_restaurants

    p, _ = provider()
    r = p.call("search_restaurants", {"query": "biryani", "address_id": "x"})
    assert r.ok and parse_restaurants(r.data)


@pytest.mark.parametrize(
    "tool",
    [
        "update_food_cart", "update_cart", "place_food_order", "checkout", "flush_food_cart",
        "clear_cart", "create_address", "delete_address", "get_payment_options",
        "check_payment_status", "confirm_order", "apply_food_coupon", "get_food_orders",
        "get_orders", "get_food_cart", "get_cart", "your_go_to_items", "bogus",
    ],
)  # fmt: skip
def test_nothing_outside_the_read_allowlist_reaches_the_network(tool):
    p, conn = provider()
    r = p.call(tool, {"restaurant_id": "1", "query": "x", "items": []})
    assert not r.ok and r.error.kind == "unknown_tool"
    assert conn.calls == []


def test_allowlist_contains_only_known_read_tools():
    writeish = ("cart", "order", "checkout", "address", "payment", "coupon", "confirm")
    for tool in READ_ALLOWLIST:
        if tool == "get_addresses":
            continue
        assert not any(k in tool for k in writeish), tool


def test_connection_cannot_be_asked_for_a_forbidden_tool_via_params():
    p, conn = provider()
    p.call("search_restaurants", {"query": "x", "address_id": "a", "tool": "place_food_order"})
    assert all(c[1] == "search_restaurants" for c in conn.calls)


def test_addresses_are_paged_and_merged():
    p, conn = provider(n_addresses=3)
    base = conn.world.addresses[0]
    conn.world.addresses = [{**base, "id": f"addr_{i}"} for i in range(1, 15)]
    r = p.call("list_addresses", {})
    assert r.ok and len(r.data["addresses"]) == 14
    assert [c[2]["page"] for c in conn.calls] == [1, 2]


def test_timeout_and_errors_are_mapped_without_echoing_text():
    p, conn = provider()
    conn.fail = CallTimeout("search_restaurants")
    assert p.call("search_restaurants", {"query": "x"}).error.kind == "timeout"
    conn.fail = RuntimeError("secret address 12 Fake Street, phone 9876543210")
    r = p.call("search_restaurants", {"query": "x"})
    assert r.error.kind == "error"
    assert "Fake Street" not in r.error.message and "9876543210" not in r.error.message


def test_bad_params_are_rejected_before_the_network():
    p, conn = provider()
    assert p.call("search_restaurants", {"query": ""}).error.kind == "bad_params"
    assert p.call("get_menu", {}).error.kind == "bad_params"
    assert conn.calls == []


def test_dry_run_end_to_end_never_writes():
    def act(kind, **a):
        return {"action": kind, "args": a, "rationale": "t"}

    conn = FakeConnection()
    prov = SwiggyProvider(conn, mode="dry_run")
    rid = conn.world.restaurants[0].id
    llm = FakeLLM([
        act("tool_call", name="search_restaurants", params={"query": "biryani"}),
        act("tool_call", name="get_menu", params={"restaurant_id": rid}),
        lambda v: act(
            "propose_plan", path="order_in", reason="r", restaurant_id=rid,
            items=[{"id": next(i["id"] for t in v["untrusted_data"]["tool_results"]
                               if t["tool"] == "get_menu" for i in t["result"]["items"]
                               if i["in_stock"] and not i["variants"] and not i["addons"]),
                    "qty": 1}],
        ),
    ])  # fmt: skip
    agent = Agent(llm, prov)
    state = agent.start("hungry")
    agent.run(state)
    assert state.phase == "AWAITING_APPROVAL" and state.plan.mode_notes
    agent.approve(state)
    assert state.outcome["kind"] == "dry_run_preview"
    assert {c[1] for c in conn.calls} <= {
        "get_addresses",
        "search_restaurants",
        "get_restaurant_menu",
    }


def test_extract_payload_prefers_structured_then_json_text():
    class R:
        structured_content = None
        content = [type("B", (), {"text": '{"a": 1}'})()]

    assert extract_payload(R()) == {"a": 1}
    R.structured_content = {"b": 2}
    assert extract_payload(R()) == {"b": 2}
    R.structured_content, R.content = None, [type("B", (), {"text": "not json"})()]
    assert extract_payload(R()) is None
