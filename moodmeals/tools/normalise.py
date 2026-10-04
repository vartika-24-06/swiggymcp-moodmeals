"""Turn Swiggy's raw responses into the normalised types (design.md 5.2).

Written against the response shapes observed on 2026-10-04 (`spikes/results/
shapes_*.json`). Parsing is lenient about missing fields and strict about money:
a price that cannot be read as whole rupees drops that item (it is never guessed).
"""

from __future__ import annotations

from typing import Any

from moodmeals.models.types import MenuItem, Product, Restaurant, Variant
from moodmeals.tools.parsing import (
    map_veg_classifier,
    parse_cost_for_two,
    parse_count,
    parse_price,
    strip_ad_marker,
)


def _s(value: Any) -> str | None:
    return str(value) if isinstance(value, str | int) and not isinstance(value, bool) else None


def parse_restaurants(payload: dict[str, Any]) -> list[Restaurant]:
    """`search_restaurants` result. Names carrying "(Ad)" are marked sponsored."""
    out: list[Restaurant] = []
    for r in payload.get("restaurants") or []:
        rid = _s(r.get("id"))
        name = r.get("name")
        if not rid or not isinstance(name, str):
            continue
        clean, ad = strip_ad_marker(name)
        eta = r.get("deliveryTimeMinutes")
        out.append(
            Restaurant(
                id=rid,
                name=clean,
                cuisines=[c for c in r.get("cuisines") or [] if isinstance(c, str)],
                rating=r.get("avgRating") if isinstance(r.get("avgRating"), int | float) else None,
                rating_count=parse_count(r.get("totalRatings")),
                cost_for_two=parse_cost_for_two(r.get("costForTwo")),
                distance_km=r.get("distanceKm")
                if isinstance(r.get("distanceKm"), int | float)
                else None,
                eta_minutes=eta if isinstance(eta, int) and not isinstance(eta, bool) else None,
                open=r.get("availabilityStatus") == "OPEN",
                sponsored=ad,
            )
        )
    return out


def _menu_item(raw: dict[str, Any], restaurant_id: str, id_key: str) -> MenuItem | None:
    item_id = _s(raw.get(id_key))
    price = parse_price(raw.get("price"))
    name = raw.get("name")
    if not item_id or price is None or not isinstance(name, str):
        return None
    return MenuItem(
        id=item_id,
        restaurant_id=restaurant_id,
        name=name,
        price=price,
        veg=map_veg_classifier(raw.get("isVeg")),
        in_stock=bool(raw.get("inStock")),  # observed as the number 1
        has_variants=bool(raw.get("hasVariants")),
        has_addons=bool(raw.get("hasAddons")),
    )


def _walk_categories(categories: list[dict[str, Any]], rid: str, out: list[MenuItem]) -> None:
    for cat in categories or []:
        for raw in cat.get("items") or []:
            item = _menu_item(raw, rid, "id")
            if item:
                out.append(item)
        # Nested categories carry subcategories[] (per the tool description).
        _walk_categories(cat.get("subcategories") or [], rid, out)


def parse_menu(payload: dict[str, Any]) -> tuple[Restaurant | None, list[MenuItem]]:
    """`get_restaurant_menu` result: the restaurant header and all items on this page."""
    info = payload.get("restaurant") or {}
    rid = _s(info.get("id"))
    if not rid or not isinstance(info.get("name"), str):
        return None, []
    clean, ad = strip_ad_marker(info["name"])
    eta = info.get("deliveryTime")
    restaurant = Restaurant(
        id=rid,
        name=clean,
        cuisines=[c for c in info.get("cuisines") or [] if isinstance(c, str)],
        rating=info.get("avgRating") if isinstance(info.get("avgRating"), int | float) else None,
        rating_count=parse_count(info.get("totalRatingsString")),
        cost_for_two=parse_cost_for_two(info.get("costForTwoMessage")),
        eta_minutes=eta if isinstance(eta, int) and not isinstance(eta, bool) else None,
        open=bool(info.get("isOpen")),
        sponsored=ad,
    )
    items: list[MenuItem] = []
    _walk_categories(payload.get("categories") or [], rid, items)
    return restaurant, items


def parse_dish_search(payload: dict[str, Any], restaurant_id: str) -> list[MenuItem]:
    """`search_menu` result, scoped to one restaurant (items carry no restaurant id)."""
    out: list[MenuItem] = []
    for raw in payload.get("items") or []:
        item = _menu_item(raw, restaurant_id, "menu_item_id")
        if item:
            out.append(item)
    return out


def parse_products(payload: dict[str, Any]) -> list[Product]:
    """Instamart `search_products` result. Veg status is the weakest variant's."""
    out: list[Product] = []
    for p in payload.get("products") or []:
        pid = _s(p.get("productId"))
        name = p.get("displayName")
        if not pid or not isinstance(name, str):
            continue
        variants: list[Variant] = []
        vegs = []
        eta: int | None = None
        for v in p.get("variations") or []:
            price = v.get("price") or {}
            offer, mrp = parse_price(price.get("offerPrice")), parse_price(price.get("mrp"))
            spin = _s(v.get("spinId"))
            max_qty = v.get("maxQuantity")
            if offer is None or mrp is None or not spin or not isinstance(max_qty, int):
                continue
            if v.get("isInStockAndAvailable") is False:
                continue
            variants.append(
                Variant(
                    spin_id=spin,
                    label=str(v.get("quantityDescription") or ""),
                    price=offer,
                    mrp=mrp,
                    max_qty=max_qty,
                )
            )
            vegs.append(map_veg_classifier(v.get("vegClassifier")))
            sla = v.get("sla") or {}
            if eta is None and sla.get("unit") == "MINS":
                eta = parse_count(sla.get("value"))
        if not variants:
            continue
        badge_types = {b.get("type") for b in p.get("badges") or [] if isinstance(b, dict)}
        veg = (
            "veg"
            if all(v == "veg" for v in vegs)
            else ("non_veg" if "non_veg" in vegs else "egg" if "egg" in vegs else "unverified")
        )
        out.append(
            Product(
                id=pid,
                name=name,
                brand=p.get("brand") if isinstance(p.get("brand"), str) else None,
                variants=variants,
                veg=veg,
                sponsored=bool(p.get("isPromoted")) or "BADGE_TYPE_AD" in badge_types,
                eta_minutes=eta,
            )
        )
    return out
