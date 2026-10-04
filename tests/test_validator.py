import pytest

from moodmeals.core.ledger import Ledger
from moodmeals.core.validator import DRY_RUN_DISCLAIMER, Constraints, validate_plan
from moodmeals.models.types import MenuItem, Plan, PlanItem, Product, Restaurant, Variant


@pytest.fixture
def ledger():
    lg = Ledger()
    lg.add_restaurants(
        [
            Restaurant(id="r1", name="Sample Kitchen", eta_minutes=25, open=True),
            Restaurant(id="r2", name="Demo Dhaba", eta_minutes=40, open=True, sponsored=True),
            Restaurant(id="r3", name="Closed Cafe", open=False),
        ]
    )
    lg.add_menu_items(
        [
            MenuItem(id="m1", restaurant_id="r1", name="Moong Khichdi", price=120, veg="veg"),
            MenuItem(id="m2", restaurant_id="r1", name="Chicken Curry", price=220, veg="non_veg"),
            MenuItem(id="m3", restaurant_id="r1", name="Mystery Bowl", price=150, veg="unverified"),
            MenuItem(
                id="m4", restaurant_id="r1", name="Raita", price=40, veg="veg", in_stock=False
            ),
            MenuItem(id="m5", restaurant_id="r2", name="Dal Tadka", price=140, veg="veg"),
            MenuItem(id="m6", restaurant_id="r3", name="Tea", price=30, veg="veg"),
        ]
    )
    lg.add_products(
        [
            Product(
                id="p1",
                name="Moong Dal",
                veg="veg",
                variants=[Variant(spin_id="s1", label="500 g", price=60, mrp=70, max_qty=3)],
            ),
            Product(
                id="p2",
                name="Promo Rice",
                veg="veg",
                sponsored=True,
                variants=[Variant(spin_id="s2", label="1 kg", price=90, mrp=100, max_qty=2)],
            ),
            Product(
                id="p3",
                name="Odd Paste",
                veg="unverified",
                variants=[Variant(spin_id="s3", label="100 g", price=50, mrp=50, max_qty=2)],
            ),
        ]
    )
    return lg


def dish(entity_id="m1", name="Moong Khichdi", qty=1, price=120):
    return PlanItem(kind="dish", entity_id=entity_id, name=name, qty=qty, unit_price=price)


def product(entity_id="p1", name="Moong Dal", variant="s1", qty=1, price=60):
    return PlanItem(
        kind="product",
        entity_id=entity_id,
        name=name,
        variant_id=variant,
        qty=qty,
        unit_price=price,
    )


def order(items=None, total=120, **kw):
    base = dict(
        path="order_in",
        reason="light and quick",
        items=items or [dish()],
        item_total=total,
        restaurant_id="r1",
    )
    base.update(kw)
    return Plan(**base)


def cook(items=None, total=60, **kw):
    base = dict(
        path="cook", reason="simple dal night", items=items or [product()], item_total=total
    )
    base.update(kw)
    return Plan(**base)


def codes(result):
    return {i.code for i in result.errors}


def test_valid_order_plan_passes(ledger):
    assert validate_plan(order(), ledger).ok


def test_valid_cook_plan_passes(ledger):
    assert validate_plan(cook(), ledger).ok


def test_empty_plan_fails(ledger):
    empty = Plan(path="cook", reason="x", items=[], item_total=0)
    assert "empty_plan" in codes(validate_plan(empty, ledger))


# V1
def test_v1_unknown_dish_product_variant_and_restaurant(ledger):
    assert "unknown_entity" in codes(validate_plan(order([dish("zzz")]), ledger))
    assert "unknown_entity" in codes(validate_plan(cook([product("zzz")]), ledger))
    assert "unknown_variant" in codes(validate_plan(cook([product(variant="s99")]), ledger))
    assert "variant_missing" in codes(validate_plan(cook([product(variant=None)]), ledger))
    assert "unknown_restaurant" in codes(validate_plan(order(restaurant_id="r99"), ledger))
    assert "restaurant_mismatch" in codes(validate_plan(order(restaurant_id="r2"), ledger))
    assert "restaurant_missing" in codes(validate_plan(order(restaurant_id=None), ledger))


def test_v1_name_must_match_the_tool(ledger):
    assert "name_mismatch" in codes(validate_plan(order([dish(name="Invented Dish")]), ledger))


# V2
def test_v2_price_and_total_are_recomputed(ledger):
    assert "price_mismatch" in codes(validate_plan(order([dish(price=100)], total=100), ledger))
    assert "total_mismatch" in codes(validate_plan(order([dish(qty=2)], total=120), ledger))
    assert validate_plan(order([dish(qty=2)], total=240), ledger).ok


# V3
def test_v3_availability_and_max_quantity(ledger):
    assert "restaurant_closed" in codes(
        validate_plan(order([dish("m6", "Tea", price=30)], total=30, restaurant_id="r3"), ledger)
    )
    assert "out_of_stock" in codes(
        validate_plan(order([dish("m4", "Raita", price=40)], total=40), ledger)
    )
    assert "quantity_over_max" in codes(validate_plan(cook([product(qty=4)], total=240), ledger))
    assert validate_plan(cook([product(qty=3)], total=180), ledger).ok


# V4
def test_v4_veg_constraint_rejects_unverified_and_non_veg(ledger):
    veg = Constraints(veg=True)
    assert "not_veg" in codes(
        validate_plan(order([dish("m2", "Chicken Curry", price=220)], total=220), ledger, veg)
    )
    assert "unverified_veg" in codes(
        validate_plan(order([dish("m3", "Mystery Bowl", price=150)], total=150), ledger, veg)
    )
    assert "unverified_veg" in codes(
        validate_plan(cook([product("p3", "Odd Paste", "s3", price=50)], total=50), ledger, veg)
    )
    assert validate_plan(order(), ledger, veg).ok


def test_v4_budget_and_exclusions(ledger):
    assert "over_budget" in codes(validate_plan(order(), ledger, Constraints(budget=100)))
    assert validate_plan(order(), ledger, Constraints(budget=120)).ok
    assert "excluded_item" in codes(
        validate_plan(order(), ledger, Constraints(exclusions=["khichdi"]))
    )


# V5
def test_v5_single_basket_and_kinds(ledger):
    mixed = order([dish(), dish("m5", "Dal Tadka", price=140)], total=260)
    assert "multiple_restaurants" in codes(validate_plan(mixed, ledger))
    assert "wrong_kind" in codes(validate_plan(order([product()], total=60), ledger))
    assert "wrong_kind" in codes(validate_plan(cook([dish()], total=120), ledger))


# V6
def test_v6_eta_must_equal_the_tool_value(ledger):
    assert validate_plan(order(eta_minutes=25), ledger).ok
    assert "eta_mismatch" in codes(validate_plan(order(eta_minutes=15), ledger))


# V7
def test_v7_assumptions_required_when_info_missing(ledger):
    needed = ["party size", "budget"]
    assert "assumptions_missing" in codes(
        validate_plan(order(), ledger, required_assumptions=needed)
    )
    assert validate_plan(
        order(assumptions=["party of 1", "no budget limit"]), ledger, required_assumptions=needed
    ).ok


# V8
def test_v8_sponsored_is_a_warning_and_never_the_reason(ledger):
    plan = order([dish("m5", "Dal Tadka", price=140)], total=140, restaurant_id="r2")
    result = validate_plan(plan, ledger)
    assert result.ok and any(w.code == "sponsored" for w in result.warnings)
    assert "sponsored_in_reason" in codes(
        validate_plan(order(reason="a promoted pick, great value"), ledger)
    )
    promo = cook([product("p2", "Promo Rice", "s2", price=90)], total=90)
    assert any(w.code == "sponsored" for w in validate_plan(promo, ledger).warnings)


# V9
def test_v9_dry_run_needs_the_exact_disclaimer(ledger):
    assert "disclaimer_missing" in codes(validate_plan(order(), ledger, mode="dry_run"))
    ok = order(mode_notes=[DRY_RUN_DISCLAIMER])
    assert validate_plan(ok, ledger, mode="dry_run").ok
    assert "disclaimer_missing" in codes(
        validate_plan(order(mode_notes=["Item total only."]), ledger, mode="dry_run")
    )


def test_v9_no_estimated_fees_or_taxes(ledger):
    for text in ("plus about 40 delivery fee", "taxes extra", "GST will apply"):
        assert "estimated_fees" in codes(validate_plan(order(reason=text), ledger))
    # The disclaimer itself mentions fees and taxes and must not trip the check.
    assert validate_plan(order(mode_notes=[DRY_RUN_DISCLAIMER]), ledger, mode="dry_run").ok


# Soft checks
def test_soft_warnings(ledger):
    result = validate_plan(order(), ledger, Constraints(party_size=4))
    assert {w.code for w in result.warnings} >= {"small_portion", "price_per_person"}
    assert result.ok
