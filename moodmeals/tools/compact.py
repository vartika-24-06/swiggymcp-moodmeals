"""Compaction: the small, PII-free view of tool results the model sees (design DQ2, R7.2).

Built from the normalised types, so anything not in the type (image URLs, descriptions,
badges such as "buy again", ratings text, area names) never reaches the model.
"""

from __future__ import annotations

import json
from typing import Any

from moodmeals.models.types import MenuItem, Product, Restaurant


def view_restaurants(items: list[Restaurant]) -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "name": r.name,
            "cuisines": r.cuisines[:3],
            "rating": r.rating,
            "ratings": r.rating_count,
            "cost_for_two": r.cost_for_two,
            "eta_min": r.eta_minutes,
            "open": r.open,
            "sponsored": r.sponsored,
        }
        for r in items
    ]


def view_menu_items(items: list[MenuItem]) -> list[dict[str, Any]]:
    return [
        {
            "id": m.id,
            "name": m.name,
            "price": m.price,
            "veg": m.veg,
            "in_stock": m.in_stock,
            "variants": m.has_variants,
            "addons": m.has_addons,
        }
        for m in items
    ]


def view_products(items: list[Product]) -> list[dict[str, Any]]:
    return [
        {
            "id": p.id,
            "name": p.name,
            "brand": p.brand,
            "veg": p.veg,
            "sponsored": p.sponsored,
            "eta_min": p.eta_minutes,
            "variants": [
                {"id": v.spin_id, "size": v.label, "price": v.price, "max_qty": v.max_qty}
                for v in p.variants
            ],
        }
        for p in items
    ]


def to_text(view: list[dict[str, Any]]) -> str:
    """Compact JSON (no spaces) as it would be sent to the model."""
    return json.dumps(view, ensure_ascii=False, separators=(",", ":"))
