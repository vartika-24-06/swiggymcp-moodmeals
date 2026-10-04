"""Normalised data types (design.md section 5.2).

These are what the rest of the code and the model's compact view are built from.
Swiggy's raw responses are converted into these by the parsers in
`moodmeals.tools.parsing`. All money is whole rupees as `int`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Anything unknown or invalid is "unverified", never "veg" (R7.4).
Veg = Literal["veg", "egg", "non_veg", "unverified"]
Path = Literal["cook", "order_in"]
PlanItemKind = Literal["dish", "product"]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Restaurant(_Frozen):
    id: str
    name: str
    cuisines: list[str] = Field(default_factory=list)
    rating: float | None = None
    rating_count: int | None = None
    cost_for_two: int | None = None
    distance_km: float | None = None
    eta_minutes: int | None = None
    open: bool = True
    sponsored: bool = False


class MenuItem(_Frozen):
    id: str
    restaurant_id: str
    name: str
    price: int
    veg: Veg = "unverified"
    in_stock: bool = True
    has_variants: bool = False
    has_addons: bool = False


class Variant(_Frozen):
    spin_id: str
    label: str
    price: int
    mrp: int
    max_qty: int


class Product(_Frozen):
    id: str
    name: str
    brand: str | None = None
    variants: list[Variant] = Field(default_factory=list)
    veg: Veg = "unverified"
    sponsored: bool = False
    eta_minutes: int | None = None


class PlanItem(_Frozen):
    kind: PlanItemKind
    entity_id: str
    name: str
    variant_id: str | None = None
    qty: int = Field(ge=1)
    unit_price: int = Field(ge=0)


class Plan(_Frozen):
    path: Path
    reason: str
    items: list[PlanItem]
    item_total: int = Field(ge=0)
    restaurant_id: str | None = None  # order-in plans
    eta_minutes: int | None = None  # must equal the tool's value (R5.6)
    assumptions: list[str] = Field(default_factory=list)
    mode_notes: list[str] = Field(default_factory=list)
