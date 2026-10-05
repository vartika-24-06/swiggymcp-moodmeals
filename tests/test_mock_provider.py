import json
import re

import pytest

from moodmeals.providers.gate import WriteAction, WriteGate
from moodmeals.providers.mock import MockProvider
from moodmeals.providers.switches import Switches
from moodmeals.providers.world import BRANDS, RESTAURANT_NAMES
from moodmeals.tools.normalise import (
    parse_dish_search,
    parse_menu,
    parse_products,
    parse_restaurants,
)

REAL_NAMES = [
    "KFC", "McDonald", "Domino", "Pizza Hut", "Haldiram", "Amul", "Tata", "Aashirvaad",
    "Swiggy", "Zomato", "Burger King", "Subway", "Starbucks", "Nestle", "Britannia",
    "Fortune", "MDH", "Everest", "Mother Dairy",
    "Delhi", "Mumbai", "Bangalore", "Gurgaon", "Noida", "Dehradun", "Pune", "Hyderabad",
    "Chennai", "Kolkata",
]  # fmt: skip


def mp(seed=0, **kw):
    return MockProvider(seed, Switches(**kw))


def data(provider, tool, **params):
    r = provider.call(tool, params)
    assert r.ok, r.error
    return r.data


def first_restaurant_id(p):
    return data(p, "search_restaurants", query="khichdi")["restaurants"][0]["id"]


# ---- T2.1: the world --------------------------------------------------------


def all_payloads(p):
    out = [
        data(p, "list_addresses"),
        data(p, "search_restaurants", query="biryani"),
        data(p, "search_products", query="dal"),
    ]
    rid = first_restaurant_id(p)
    out += [
        data(p, "get_menu", restaurant_id=rid, page_size=8),
        data(p, "search_dish", query="khichdi", restaurant_id=rid),
    ]
    return out


def test_same_seed_same_world_different_seed_different_world():
    assert all_payloads(mp(3)) == all_payloads(mp(3))
    assert all_payloads(mp(3)) != all_payloads(mp(4))


def test_world_is_invented_only():
    blob = json.dumps(all_payloads(mp(1)), ensure_ascii=False)
    for real in REAL_NAMES:
        assert real.lower() not in blob.lower(), real
    # every restaurant and brand comes from the invented lists
    for r in mp(1).world.restaurants:
        assert r.name in RESTAURANT_NAMES
    assert all(p.brand in BRANDS for p in mp(1).world.products)
    # no phone-like or email-like values
    assert not re.search(r"\+?\d(?:[\s-]?\d){9,}", blob)
    assert "@" not in blob


def test_default_payloads_parse_without_drops():
    p = mp(2)
    s = data(p, "search_restaurants", query="khichdi")
    assert len(parse_restaurants(s)) == len(s["restaurants"]) == 10
    rid = first_restaurant_id(p)
    menu = data(p, "get_menu", restaurant_id=rid, page_size=8)
    restaurant, items = parse_menu(menu)
    raw_items = sum(len(c["items"]) for c in menu["categories"])
    assert restaurant and len(items) == raw_items > 0
    dishes = data(p, "search_dish", query="khichdi", restaurant_id=rid)
    assert len(parse_dish_search(dishes, rid)) == len(dishes["items"]) > 0
    prods = data(p, "search_products", query="dal")
    assert len(parse_products(prods)) == len(prods["products"])


def test_search_is_loose_but_ranks_matches_first():
    p = mp(0)
    rs = data(p, "search_restaurants", query="khichdi")["restaurants"]
    matches = [
        r for r in mp(0).world.restaurants if any(d.name.endswith("Khichdi") for d in r.dishes)
    ]
    assert matches and rs[0]["id"] in {m.id for m in matches}


def test_paging_does_not_repeat_results():
    p = mp(0)
    a = data(p, "search_restaurants", query="khichdi", offset=0)
    assert a["hasMore"] and a["nextOffset"] == 10
    b = data(p, "search_restaurants", query="khichdi", offset=10)
    assert not ({r["id"] for r in a["restaurants"]} & {r["id"] for r in b["restaurants"]})
    assert not b["hasMore"] and "nextOffset" not in b


def test_menu_page_size_is_capped_at_eight():
    p = mp(0)
    assert data(p, "get_menu", restaurant_id=first_restaurant_id(p), page_size=50)["pageSize"] <= 8


def test_dish_search_veg_filter():
    p = mp(0)
    rid = first_restaurant_id(p)
    veg = data(p, "search_dish", query="a", restaurant_id=rid, veg_only=True)
    assert veg["items"] and all(i["isVeg"] for i in veg["items"])


def test_addresses_one_or_several():
    one = data(MockProvider(0, n_addresses=1), "list_addresses")
    three = data(MockProvider(0, n_addresses=3), "list_addresses")
    assert one["total"] == 1 and one["resolution"]["needsUserClarification"] is False
    assert three["total"] == 3 and three["resolution"]["needsUserClarification"] is True


# ---- T2.2: failure switches -------------------------------------------------


def test_switch_timeout_and_error():
    r = mp(fail_tools={"get_menu": "timeout"}).call("get_menu", {"restaurant_id": "1"})
    assert not r.ok and r.error.kind == "timeout" and r.latency_ms >= 15000
    r = mp(fail_tools={"search_products": "error"}).call("search_products", {"query": "dal"})
    assert not r.ok and r.error.kind == "error"
    # other tools are unaffected
    assert mp(fail_tools={"get_menu": "timeout"}).call("search_products", {"query": "dal"}).ok


def test_switch_empty_search():
    p = mp(empty_search=True)
    assert parse_restaurants(data(p, "search_restaurants", query="khichdi")) == []
    assert parse_products(data(p, "search_products", query="dal")) == []


def test_switch_partial_menu_drops_items_and_a_category():
    full, partial = mp(), mp(partial_menu=True)
    rid = first_restaurant_id(full)
    f = data(full, "get_menu", restaurant_id=rid, page_size=8)
    q = data(partial, "get_menu", restaurant_id=rid, page_size=8)
    assert q["totalCategories"] < f["totalCategories"]
    n_raw = sum(len(c["items"]) for c in q["categories"])
    assert len(parse_menu(q)[1]) < n_raw  # unreadable prices are dropped, not guessed


def test_switch_all_closed():
    p = mp(all_closed=True)
    rs = parse_restaurants(data(p, "search_restaurants", query="khichdi"))
    assert rs and not any(r.open for r in rs)
    restaurant, _ = parse_menu(data(p, "get_menu", restaurant_id=rs[0].id))
    assert restaurant and not restaurant.open


def test_switch_out_of_stock():
    p = mp(out_of_stock=frozenset({"*"}))
    rid = first_restaurant_id(p)
    _, items = parse_menu(data(p, "get_menu", restaurant_id=rid, page_size=8))
    assert items and not any(i.in_stock for i in items)
    assert parse_products(data(p, "search_products", query="dal")) == []  # nothing left to offer


def test_switch_ad_rate():
    none = parse_restaurants(data(mp(ad_rate=0.0), "search_restaurants", query="x"))
    every = parse_restaurants(data(mp(ad_rate=1.0), "search_restaurants", query="x"))
    assert not any(r.sponsored for r in none) and all(r.sponsored for r in every)
    assert all(
        p.sponsored for p in parse_products(data(mp(ad_rate=1.0), "search_products", query="dal"))
    )


def test_switch_invalid_veg_classifier():
    default = parse_products(data(mp(), "search_products", query="dal"))
    assert {p.veg for p in default} <= {"veg", "egg"}
    bad = parse_products(data(mp(invalid_veg_rate=1.0), "search_products", query="dal"))
    assert bad and all(p.veg == "unverified" for p in bad)


def test_switch_max_qty_limits():
    ps = parse_products(data(mp(max_qty=1), "search_products", query="dal"))
    assert ps and all(v.max_qty == 1 for p in ps for v in p.variants)


def test_switch_buy_again_badges():
    on = json.dumps(data(mp(buy_again_badges=True), "search_products", query="dal"))
    off = json.dumps(data(mp(buy_again_badges=False), "search_products", query="dal"))
    assert "BUY AGAIN" in on and "BUY AGAIN" not in off


def test_switch_price_scale():
    cheap = parse_products(data(mp(), "search_products", query="dal"))
    dear = parse_products(data(mp(price_scale=10), "search_products", query="dal"))
    assert dear[0].variants[0].price > 5 * cheap[0].variants[0].price


# ---- T2.3: provider behaviour -----------------------------------------------


def test_bad_params_and_unknown_tool_and_not_found():
    p = mp()
    assert p.call("search_restaurants", {"query": ""}).error.kind == "bad_params"
    assert p.call("search_restaurants", {"query": "x" * 200}).error.kind == "bad_params"
    assert p.call("search_restaurants", {"query": "x", "offset": -1}).error.kind == "bad_params"
    assert p.call("get_menu", {}).error.kind == "bad_params"
    assert p.call("drop_database", {}).error.kind == "unknown_tool"
    assert p.call("get_menu", {"restaurant_id": "0"}).error.kind == "not_found"


def test_instamart_update_cart_replaces_the_whole_cart():
    p = mp()
    assert data(p, "get_cart_state")["empty"] is True
    p.call("update_cart", {"items": [{"spin_id": "a", "qty": 1}]})
    r = p.call("update_cart", {"items": [{"spin_id": "b", "qty": 2}]})
    assert r.data["replaced_cart"] is True
    assert p.im_cart == [{"spin_id": "b", "qty": 2}]
    assert data(p, "get_cart_state")["empty"] is False


def test_simulated_orders_stay_in_memory_and_need_a_cart():
    p = mp()
    assert p.call("checkout", {}).error.kind == "bad_params"
    p.call("update_cart", {"items": [{"spin_id": "a", "qty": 1}]})
    assert p.call("checkout", {}).data["simulated"] is True
    assert len(p.sim_orders) == 1 and p.im_cart == []
    p.call("update_food_cart", {"restaurant_id": "1", "items": [{"id": "x", "qty": 1}]})
    assert p.call("place_food_order", {}).ok and len(p.sim_orders) == 2


def test_writes_reach_the_provider_only_through_an_approved_gate():
    p = mp()
    action = WriteAction("update_cart", {"items": [{"spin_id": "a", "qty": 1}]})
    dry = WriteGate("dry_run", lambda t, a: p.call(t, a))
    dry.execute(action, dry.issue_approval(action))
    assert "update_cart" not in p.calls  # dry-run never reached the provider
    gate = WriteGate("mock", lambda t, a: p.call(t, a))
    assert gate.execute(action, None).status == "blocked"
    assert gate.execute(action, gate.issue_approval(action)).status == "simulated"
    assert p.calls.count("update_cart") == 1


@pytest.mark.parametrize("tool", ["search_restaurants", "search_products"])
def test_latency_is_reported_and_deterministic(tool):
    a, b = mp(5), mp(5)
    assert (
        a.call(tool, {"query": "dal"}).latency_ms == b.call(tool, {"query": "dal"}).latency_ms > 0
    )
