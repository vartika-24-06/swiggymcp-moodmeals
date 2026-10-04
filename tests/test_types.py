import pytest
from pydantic import ValidationError

from moodmeals.models.types import MenuItem, Plan, PlanItem, Product, Restaurant, Variant


def test_unknown_veg_defaults_to_unverified():
    assert MenuItem(id="m1", restaurant_id="r1", name="Khichdi", price=120).veg == "unverified"
    assert Product(id="p1", name="Moong dal").veg == "unverified"


def test_types_are_frozen_and_strict():
    r = Restaurant(id="r1", name="Sample Kitchen")
    with pytest.raises(ValidationError):
        r.name = "Other"
    with pytest.raises(ValidationError):
        Restaurant(id="r1", name="x", surprise=1)


def test_plan_round_trips_to_json():
    plan = Plan(
        path="order_in",
        reason="light and quick",
        items=[PlanItem(kind="dish", entity_id="m1", name="Khichdi", qty=1, unit_price=120)],
        item_total=120,
    )
    assert Plan.model_validate_json(plan.model_dump_json()) == plan


def test_plan_item_quantity_must_be_positive():
    with pytest.raises(ValidationError):
        PlanItem(kind="dish", entity_id="m1", name="x", qty=0, unit_price=10)


def test_variant_fields():
    v = Variant(spin_id="s1", label="500 g", price=60, mrp=70, max_qty=3)
    assert v.max_qty == 3
