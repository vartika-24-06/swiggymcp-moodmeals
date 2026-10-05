"""Deterministic plan validator V1 to V9 (design.md section 8; R8).

Pure functions, no model. The validator never edits a plan: it only returns
structured errors (fed back to the model) and warnings (shown to the user).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from moodmeals.core.ledger import Ledger
from moodmeals.models.types import Plan, PlanItem

DRY_RUN_DISCLAIMER = (
    "Item total only. Delivery fees, taxes and discounts are not shown in dry-run. "
    "The final bill is shown only in live mode, before you place an order."
)
_FEE_WORDS = re.compile(
    r"\b(delivery\s+(?:fee|charge)s?|platform\s+fee|packaging|taxes|tax|gst)\b", re.IGNORECASE
)
_SPONSORED_WORDS = re.compile(r"\b(sponsored|promoted|promotion|advert\w*|ad)\b", re.IGNORECASE)


class Constraints(BaseModel):
    """Hard constraints the person stated (or the code defaulted)."""

    veg: bool = False
    budget: int | None = Field(default=None, ge=0)  # item total only (design section 3)
    exclusions: list[str] = Field(default_factory=list)  # name fragments, case-insensitive
    party_size: int | None = Field(default=None, ge=1)


@dataclass(frozen=True)
class Issue:
    check: str  # "V1" .. "V9"
    code: str
    message: str
    entity: str | None = None


@dataclass
class ValidationResult:
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class _Resolved:
    item: PlanItem
    name: str
    price: int
    veg: str
    sponsored: bool
    in_stock: bool
    restaurant_id: str | None
    max_qty: int | None


def validate_plan(
    plan: Plan,
    ledger: Ledger,
    constraints: Constraints | None = None,
    *,
    mode: Literal["mock", "dry_run", "live"] = "mock",
    required_assumptions: list[str] | None = None,
) -> ValidationResult:
    c = constraints or Constraints()
    res = ValidationResult()
    err = res.errors

    if not plan.items:
        err.append(Issue("V5", "empty_plan", "The plan has no items."))
        return res

    resolved: list[_Resolved] = []
    # V1 entity exists, V5 kind matches the path
    for it in plan.items:
        if plan.path == "order_in" and it.kind != "dish":
            err.append(Issue("V5", "wrong_kind", "Order-in plans use dishes only.", it.entity_id))
        if plan.path == "cook" and it.kind != "product":
            err.append(Issue("V5", "wrong_kind", "Cook plans use products only.", it.entity_id))
        r = _resolve(it, ledger, err)
        if r:
            resolved.append(r)

    # V1 (live only): a cart write cannot carry a variant choice, so dishes with variants are out
    if mode == "live" and plan.path == "order_in":
        for it in plan.items:
            m = ledger.menu_items.get(it.entity_id)
            if m is not None and m.has_variants:
                err.append(
                    Issue(
                        "V1",
                        "variants_unsupported",
                        f"{m.name} needs an option choice that cannot be sent. Pick a dish "
                        "without variants.",
                        it.entity_id,
                    )
                )

    restaurants = {r.restaurant_id for r in resolved if r.restaurant_id}
    # V5 single basket
    if plan.path == "order_in":
        if len(restaurants) > 1:
            err.append(
                Issue("V5", "multiple_restaurants", "An order-in plan must use one restaurant.")
            )
        if plan.restaurant_id is None:
            err.append(Issue("V1", "restaurant_missing", "The plan names no restaurant."))
        elif plan.restaurant_id not in ledger.restaurants:
            err.append(
                Issue(
                    "V1", "unknown_restaurant", "Restaurant not seen this run.", plan.restaurant_id
                )
            )
        elif restaurants and restaurants != {plan.restaurant_id}:
            err.append(Issue("V1", "restaurant_mismatch", "Items belong to another restaurant."))

    # V2 price match and recomputed total
    recomputed = 0
    for r in resolved:
        if r.item.unit_price != r.price:
            err.append(
                Issue(
                    "V2", "price_mismatch", f"{r.name}: tool price is {r.price}.", r.item.entity_id
                )
            )
        recomputed += r.item.qty * r.price
    if len(resolved) == len(plan.items) and plan.item_total != recomputed:
        err.append(Issue("V2", "total_mismatch", f"Item total should be {recomputed}."))

    # V3 availability
    rest = ledger.restaurants.get(plan.restaurant_id or "") if plan.path == "order_in" else None
    if rest is not None and not rest.open:
        err.append(Issue("V3", "restaurant_closed", "The restaurant is not open.", rest.id))
    for r in resolved:
        if not r.in_stock:
            err.append(Issue("V3", "out_of_stock", f"{r.name} is out of stock.", r.item.entity_id))
        if r.max_qty is not None and r.item.qty > r.max_qty:
            err.append(
                Issue("V3", "quantity_over_max", f"{r.name}: max {r.max_qty}.", r.item.entity_id)
            )

    # V4 hard constraints
    if c.veg:
        for r in resolved:
            if r.veg == "unverified":
                err.append(
                    Issue(
                        "V4",
                        "unverified_veg",
                        f"{r.name}: veg status not verified.",
                        r.item.entity_id,
                    )
                )
            elif r.veg != "veg":
                err.append(Issue("V4", "not_veg", f"{r.name} is not vegetarian.", r.item.entity_id))
    if c.budget is not None and recomputed > c.budget:
        err.append(
            Issue("V4", "over_budget", f"Item total {recomputed} is over the budget {c.budget}.")
        )
    for ex in c.exclusions:
        for r in resolved:
            if ex.strip() and ex.strip().lower() in r.name.lower():
                err.append(
                    Issue(
                        "V4",
                        "excluded_item",
                        f"{r.name} matches exclusion '{ex}'.",
                        r.item.entity_id,
                    )
                )

    # V6 delivery estimate
    if plan.eta_minutes is not None:
        known = rest.eta_minutes if rest else None
        if known is None or plan.eta_minutes != known:
            err.append(Issue("V6", "eta_mismatch", f"The tool's delivery estimate is {known}."))

    # V7 assumptions
    if required_assumptions and not plan.assumptions:
        err.append(
            Issue(
                "V7",
                "assumptions_missing",
                "List the assumptions made: " + ", ".join(required_assumptions),
            )
        )

    # V8 sponsored: computed from tool data, shown, never the reason
    for r in resolved:
        if r.sponsored:
            res.warnings.append(
                Issue("V8", "sponsored", f"{r.name} is sponsored.", r.item.entity_id)
            )
    if rest is not None and rest.sponsored:
        res.warnings.append(Issue("V8", "sponsored", f"{rest.name} is sponsored.", rest.id))
    if _SPONSORED_WORDS.search(plan.reason):
        err.append(
            Issue("V8", "sponsored_in_reason", "Sponsorship must not be part of the reason.")
        )

    # V9 mode note
    if mode == "dry_run" and DRY_RUN_DISCLAIMER not in plan.mode_notes:
        err.append(
            Issue("V9", "disclaimer_missing", "Dry-run plans must carry the item-total disclaimer.")
        )
    scan = " ".join([plan.reason, *plan.assumptions, *plan.mode_notes]).replace(
        DRY_RUN_DISCLAIMER, ""
    )
    if _FEE_WORDS.search(scan):
        err.append(
            Issue("V9", "estimated_fees", "Do not mention or estimate fees, taxes or discounts.")
        )

    # Soft checks (warnings only)
    if c.party_size:
        qty = sum(i.qty for i in plan.items)
        if c.party_size >= 4 and qty <= 1:
            res.warnings.append(
                Issue("SOFT", "small_portion", "Very few items for this party size.")
            )
        res.warnings.append(
            Issue(
                "SOFT",
                "price_per_person",
                f"About {recomputed // c.party_size} per person (item total).",
            )
        )
    return res


def _resolve(it: PlanItem, ledger: Ledger, err: list[Issue]) -> _Resolved | None:
    if it.kind == "dish":
        m = ledger.menu_items.get(it.entity_id)
        if m is None:
            err.append(Issue("V1", "unknown_entity", "Dish not seen in this run.", it.entity_id))
            return None
        if m.restaurant_id not in ledger.restaurants:
            err.append(
                Issue(
                    "V1", "unknown_restaurant", "Dish's restaurant not seen this run.", it.entity_id
                )
            )
        if it.name.strip().lower() != m.name.strip().lower():
            err.append(Issue("V1", "name_mismatch", f"Name should be '{m.name}'.", it.entity_id))
        return _Resolved(it, m.name, m.price, m.veg, False, m.in_stock, m.restaurant_id, None)
    p = ledger.products.get(it.entity_id)
    if p is None:
        err.append(Issue("V1", "unknown_entity", "Product not seen in this run.", it.entity_id))
        return None
    if it.name.strip().lower() != p.name.strip().lower():
        err.append(Issue("V1", "name_mismatch", f"Name should be '{p.name}'.", it.entity_id))
    if not it.variant_id:
        err.append(Issue("V1", "variant_missing", "Choose a returned pack size.", it.entity_id))
        return None
    v = next((v for v in p.variants if v.spin_id == it.variant_id), None)
    if v is None:
        err.append(
            Issue("V1", "unknown_variant", "Pack size was not returned by the tool.", it.entity_id)
        )
        return None
    return _Resolved(it, p.name, v.price, p.veg, p.sponsored, True, None, v.max_qty)
