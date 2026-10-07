"""The synthetic world and the payload builders (tasks T2.1; requirements A4, A5, R12.3).

Everything here is invented: restaurant names, dishes, brands and addresses are
placeholders, never real businesses or people. A world is a pure function of its seed.
Payload builders produce the response shapes Swiggy's tools returned on 2026-10-04.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

from moodmeals.providers.switches import Switches

RESTAURANT_NAMES = [
    "Sample Dhaba", "Demo Kitchen", "Test Tiffin Co", "Mock Biryani House", "Placeholder Pizzeria",
    "Example Thali Corner", "Fiction Fry-up", "Imaginary Idli Bar", "Sandbox Sweets",
    "Pretend Paratha Point", "Dummy Dosa Den", "Synthetic Subzi", "Faux Falafel",
    "Notional Noodles", "Hypothetical Haleem",
]  # fmt: skip
BRANDS = ["Sample Foods", "Demo Mills", "Test Farms", "Mock Dairy", "Example Spices"]

# cuisine -> [(dish, base price in rupees, is_veg)]
DISHES: dict[str, list[tuple[str, int, bool]]] = {
    "North Indian": [("Moong Khichdi", 150, True), ("Dal Tadka", 180, True),
                     ("Paneer Butter Masala", 260, True), ("Butter Chicken", 320, False),
                     ("Jeera Rice", 120, True), ("Tandoori Roti", 30, True),
                     ("Mixed Veg Curry", 190, True), ("Chicken Curry", 300, False)],
    "South Indian": [("Masala Dosa", 120, True), ("Idli Sambar", 90, True),
                     ("Curd Rice", 100, True), ("Lemon Rice", 110, True),
                     ("Egg Dosa", 140, False)],
    "Chinese": [("Veg Hakka Noodles", 170, True), ("Chicken Fried Rice", 230, False),
                ("Paneer Chilli", 240, True), ("Manchow Soup", 120, True)],
    "Biryani": [("Veg Biryani", 220, True), ("Chicken Biryani", 290, False), ("Raita", 50, True)],
    "Pizza": [("Margherita Pizza", 250, True), ("Farmhouse Pizza", 340, True),
              ("Chicken Pizza", 360, False), ("Garlic Bread", 150, True)],
    "Desserts": [("Gulab Jamun", 90, True), ("Brownie", 130, True), ("Kheer", 100, True)],
    "Thalis": [("Veg Thali", 200, True), ("Rajma Chawal", 160, True), ("Kadhi Chawal", 150, True),
               ("Khichdi Thali", 180, True)],
    "Fast Food": [("Veg Burger", 120, True), ("Paneer Wrap", 150, True), ("French Fries", 100, True)],
    "Healthy": [("Quinoa Salad", 220, True), ("Sprouts Bowl", 160, True), ("Oats Khichdi", 140, True)],
    "Street Food": [("Pav Bhaji", 140, True), ("Aloo Tikki Chaat", 90, True), ("Samosa", 40, True)],
}  # fmt: skip

# product -> ([(pack label, offer price)], veg classifier suffix). The EGG spelling is a guess.
PRODUCTS: list[tuple[str, list[tuple[str, int]], str]] = [
    ("Moong Dal", [("500 g", 60), ("1 kg", 115)], "VEG"),
    ("Toor Dal", [("500 g", 70), ("1 kg", 135)], "VEG"),
    ("Basmati Rice", [("1 kg", 95), ("5 kg", 440)], "VEG"),
    ("Whole Wheat Atta", [("1 kg", 55), ("5 kg", 260)], "VEG"),
    ("Fresh Paneer", [("200 g", 85)], "VEG"),
    ("Curd", [("400 g", 45)], "VEG"),
    ("Ghee", [("200 ml", 140), ("500 ml", 330)], "VEG"),
    ("Onion", [("1 kg", 40)], "VEG"),
    ("Tomato", [("500 g", 30)], "VEG"),
    ("Potato", [("1 kg", 35)], "VEG"),
    ("Green Peas", [("500 g", 70)], "VEG"),
    ("Spinach", [("250 g", 25)], "VEG"),
    ("Turmeric Powder", [("100 g", 38)], "VEG"),
    ("Cumin Seeds", [("100 g", 55)], "VEG"),
    ("Iodised Salt", [("1 kg", 22)], "VEG"),
    ("Sunflower Oil", [("1 l", 160), ("5 l", 760)], "VEG"),
    ("Sandwich Bread", [("400 g", 45)], "VEG"),
    ("Farm Eggs", [("6 pcs", 60), ("12 pcs", 115)], "EGG"),
    # Ready-to-eat and quick-cook items: the fallback when ordering in is not possible.
    ("Instant Poha Cup", [("80 g", 45)], "VEG"),
    ("Ready-to-Eat Dal Makhani", [("300 g", 120)], "VEG"),
    ("Ready-to-Cook Khichdi Mix", [("200 g", 75)], "VEG"),
    ("Instant Masala Noodles", [("4 pack", 60)], "VEG"),
]

FAKE_ADDRESSES = [
    ("Home", "Flat 101, Sample Heights, Demo Nagar, Testville"),
    ("Work", "Unit 7, Placeholder Park, Example Road, Testville"),
    ("Other", "House 12, Fictional Lane, Mock Colony, Testville"),
]


def _rupees(n: float) -> int:
    return max(5, int(round(n / 5) * 5))


def _count_text(n: int) -> str:
    return f"{n / 1000:.1f}K+" if n >= 1000 else str(n)


@dataclass
class Dish:
    id: str
    name: str
    price: int
    veg: bool
    cuisine: str
    has_addons: bool
    u_stock: float


@dataclass
class Rest:
    id: str
    name: str
    cuisines: list[str]
    rating: float
    rating_count: int
    cost_for_two: int
    distance_km: float
    eta_min: int
    dishes: list[Dish]
    u_ad: float


@dataclass
class Prod:
    id: str
    name: str
    brand: str
    variants: list[dict[str, Any]]
    veg_suffix: str
    u_promo: float
    u_invalid: float
    u_buy_again: float
    eta_min: int


@dataclass
class World:
    seed: int
    restaurants: list[Rest] = field(default_factory=list)
    products: list[Prod] = field(default_factory=list)
    addresses: list[dict[str, Any]] = field(default_factory=list)

    def restaurant(self, rid: str) -> Rest | None:
        return next((r for r in self.restaurants if r.id == rid), None)


def build_world(seed: int = 0, n_addresses: int = 1) -> World:
    rng = random.Random(seed)
    world = World(seed)
    used: set[int] = set()
    names = rng.sample(RESTAURANT_NAMES, len(RESTAURANT_NAMES))
    for name in names:
        rid = rng.randint(10000, 99999)
        while rid in used:
            rid = rng.randint(10000, 99999)
        used.add(rid)
        cuisines = rng.sample(sorted(DISHES), rng.randint(2, 3))
        pool = [(c, d) for c in cuisines for d in DISHES[c]]
        picked = rng.sample(pool, min(len(pool), rng.randint(10, 16)))
        dishes = [
            Dish(
                id=f"{rid}{k:03d}", name=d[0], price=_rupees(d[1] * rng.uniform(0.9, 1.15)),
                veg=d[2], cuisine=c, has_addons=rng.random() < 0.6, u_stock=rng.random(),
            )
            for k, (c, d) in enumerate(picked, start=1)
        ]  # fmt: skip
        world.restaurants.append(
            Rest(
                id=str(rid), name=name, cuisines=cuisines, rating=round(rng.uniform(3.2, 4.6), 1),
                rating_count=rng.choice([45, 230, 870, 2100, 5100, 12000]),
                cost_for_two=_rupees(rng.uniform(250, 600)), distance_km=round(rng.uniform(1, 6), 1),
                eta_min=rng.randint(18, 45), dishes=dishes, u_ad=rng.random(),
            )
        )  # fmt: skip
    for n, (name, packs, veg) in enumerate(PRODUCTS, start=1):
        variants = [
            {
                "spinId": str(700000 + n * 10 + j), "skuId": str(800000 + n * 10 + j),
                "label": label, "offer": price, "mrp": price + rng.choice([0, 5, 10, 15]),
                "max_qty": rng.choice([3, 5, 10, 20]),
            }
            for j, (label, price) in enumerate(packs)
        ]  # fmt: skip
        world.products.append(
            Prod(
                id=str(600000 + n), name=name, brand=rng.choice(BRANDS), variants=variants,
                veg_suffix=veg, u_promo=rng.random(), u_invalid=rng.random(),
                u_buy_again=rng.random(), eta_min=rng.choice([8, 10, 12, 15]),
            )
        )  # fmt: skip
    for k in range(max(1, min(n_addresses, len(FAKE_ADDRESSES)))):
        category, text = FAKE_ADDRESSES[k]
        world.addresses.append(
            {"id": f"addr_{k + 1}", "category": category, "tag": "", "address": text,
             "phone": "XXXXXXXX00"}
        )  # fmt: skip
    return world


# --------------------------------------------------------------------------- #
# Payload builders (shapes observed 2026-10-04)
# --------------------------------------------------------------------------- #


def _tokens(query: str) -> list[str]:
    return [t for t in query.lower().replace(",", " ").split() if t]


def _rank(items: list[Any], matches: list[bool]) -> list[Any]:
    """Matches first, then the rest: search relevance is loose in the real tool."""
    return [i for i, m in zip(items, matches, strict=True) if m] + [
        i for i, m in zip(items, matches, strict=True) if not m
    ]


def addresses_payload(world: World, page: int = 1, page_size: int = 10) -> dict[str, Any]:
    items = world.addresses
    start = (page - 1) * page_size
    return {
        "addresses": items[start : start + page_size],
        "total": len(items),
        "pagination": {
            "page": page, "pageSize": page_size, "total": len(items),
            "totalPages": max(1, -(-len(items) // page_size)), "hasMore": start + page_size < len(items),
        },
        "resolution": {
            "resolutionSource": "default_address", "needsUserClarification": len(items) > 1,
            "candidateAddressIds": [a["id"] for a in items], "defaultAddressId": items[0]["id"],
            "scoreUsed": 1,
        },
    }  # fmt: skip


def search_restaurants_payload(
    world: World, sw: Switches, query: str, offset: int = 0
) -> dict[str, Any]:
    toks = _tokens(query)
    rs = world.restaurants
    matches = [
        any(t in " ".join([r.name, *r.cuisines, *(d.name for d in r.dishes)]).lower() for t in toks)
        for r in rs
    ]
    ranked = [] if sw.empty_search else _rank(rs, matches)
    page = ranked[offset : offset + 10]
    out = []
    for r in page:
        ad = r.u_ad < sw.ad_rate
        entry: dict[str, Any] = {
            "id": r.id,
            "name": f"{r.name} (Ad)" if ad else r.name,
            "cuisines": r.cuisines,
            "avgRating": r.rating,
            "totalRatings": _count_text(r.rating_count),
            "costForTwo": f"₹{r.cost_for_two} for two",
            "areaName": "Demo Area",
            "distanceKm": r.distance_km,
            "deliveryTimeMinutes": r.eta_min,
            "deliveryTimeRange": f"{r.eta_min - 5}-{r.eta_min + 5} MINS",
            "imageUrl": f"https://img.example/{r.id}.jpg",
            "availabilityStatus": "CLOSED" if sw.all_closed else "OPEN",
        }
        out.append(entry)
    more = offset + 10 < len(ranked)
    return {
        "restaurants": out, "dishes": [], "total": len(out), "totalRestaurants": len(ranked),
        "totalDishes": 0, "query": query, "hasMore": more,
        **({"nextOffset": offset + 10} if more else {}),
    }  # fmt: skip


def _item_stock(d: Dish, sw: Switches) -> int:
    return 0 if ("*" in sw.out_of_stock or d.id in sw.out_of_stock) else 1


def _menu_item(d: Dish, sw: Switches, idx: int) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": d.id, "name": d.name, "price": _rupees(d.price * sw.price_scale),
        "inStock": _item_stock(d, sw), "isVeg": d.veg, "isBestseller": idx % 7 == 0,
        "rating": "4.1", "hasVariants": False, "hasAddons": d.has_addons,
        "description": f"A placeholder description for {d.name}.",
        "imageUrl": f"https://img.example/{d.id}.jpg",
    }  # fmt: skip
    if sw.partial_menu and idx % 3 == 0:
        item["price"] = "price unavailable"  # unreadable on purpose
    if sw.inject_text and idx == 1:
        item["name"] = f"{d.name} ({sw.inject_text})"
        item["description"] = sw.inject_text
    return item


def menu_payload(
    world: World, sw: Switches, restaurant_id: str, page: int = 1, page_size: int = 5
) -> dict[str, Any] | None:
    r = world.restaurant(restaurant_id)
    if r is None:
        return None
    page_size = max(1, min(page_size, 8))
    by_cuisine: dict[str, list[Dish]] = {}
    for d in r.dishes:
        by_cuisine.setdefault(d.cuisine, []).append(d)
    cats = sorted(by_cuisine.items())
    if sw.partial_menu:
        cats = cats[: max(1, len(cats) - 1)]  # a whole category is missing
    start = (page - 1) * page_size
    chunk = cats[start : start + page_size]
    idx = start * 10
    categories = []
    for title, ds in chunk:
        items = []
        for d in ds:
            idx += 1
            items.append(_menu_item(d, sw, idx))
        categories.append(
            {"title": title, "categoryId": f"cat_{title.lower().replace(' ', '_')}", "items": items,
             "totalItems": len(items), "hasMoreItems": False}
        )  # fmt: skip
    return {
        "restaurant": {
            "id": r.id, "name": r.name, "city": "Testville", "areaName": "Demo Area",
            "address": "Demo address", "cuisines": r.cuisines, "avgRating": r.rating,
            "avgRatingString": str(r.rating), "totalRatingsString": f"{_count_text(r.rating_count)} ratings",
            "costForTwoMessage": f"₹{r.cost_for_two} for two", "isOpen": not sw.all_closed,
            "deliveryTime": r.eta_min, "slaString": f"{r.eta_min - 5}-{r.eta_min + 5} MINS",
        },
        "categories": categories, "totalCategories": len(cats), "page": page,
        "pageSize": len(chunk), "hasMore": start + page_size < len(cats),
    }  # fmt: skip


def dish_search_payload(
    world: World, sw: Switches, query: str, restaurant_id: str, veg_only: bool = False,
    offset: int = 0,
) -> dict[str, Any]:  # fmt: skip
    r = world.restaurant(restaurant_id)
    toks = _tokens(query)
    ds = [] if (r is None or sw.empty_search) else r.dishes
    if veg_only:
        ds = [d for d in ds if d.veg]
    ranked = _rank(ds, [any(t in d.name.lower() for t in toks) for d in ds])
    ranked = [d for d in ranked if any(t in d.name.lower() for t in toks)] or ranked
    page = ranked[offset : offset + 10]
    more = offset + 10 < len(ranked)
    items = [
        {
            "menu_item_id": d.id, "name": d.name, "price": _rupees(d.price * sw.price_scale),
            "isVeg": d.veg, "inStock": _item_stock(d, sw), "rating": "4.1", "totalRatings": "230",
            "hasVariants": False, "hasAddons": d.has_addons, "imageUrl": f"https://img.example/{d.id}.jpg",
        }
        for d in page
    ]  # fmt: skip
    if sw.inject_text and items:
        items[0]["name"] = f"{items[0]['name']} ({sw.inject_text})"
    return {
        "items": items, "total": len(items), "query": query, "restaurantIdOfAddedItem": restaurant_id,
        "hasMore": more, **({"nextOffset": offset + 10} if more else {}), "totalItems": len(ranked),
    }  # fmt: skip


def search_products_payload(
    world: World, sw: Switches, query: str, offset: int = 0
) -> dict[str, Any]:
    toks = _tokens(query)
    ps = world.products
    ranked = (
        [] if sw.empty_search else _rank(ps, [any(t in p.name.lower() for t in toks) for p in ps])
    )
    page = ranked[offset : offset + 20]
    products = []
    for p in page:
        promoted = p.u_promo < sw.ad_rate
        badges: list[dict[str, Any]] = []
        if promoted:
            badges.append({"type": "BADGE_TYPE_AD", "text": "AD", "backgroundColor": "#FFFFFF"})
        if sw.buy_again_badges and p.u_buy_again < 0.2:
            badges.append(
                {"type": "BADGE_TYPE_TRENDING", "text": "BUY AGAIN", "backgroundColor": "#EEEEEE"}
            )
        invalid = p.u_invalid < sw.invalid_veg_rate
        stock_all = "*" in sw.out_of_stock
        variations = []
        for v in p.variants:
            in_stock = not (stock_all or v["spinId"] in sw.out_of_stock or p.id in sw.out_of_stock)
            variations.append(
                {
                    "spinId": v["spinId"], "skuId": v["skuId"], "quantityDescription": v["label"],
                    "displayName": f"{p.brand} {p.name} {v['label']}", "brandName": p.brand,
                    "price": {"mrp": v["mrp"], "offerPrice": _rupees(v["offer"] * sw.price_scale),
                              "unitLevelPrice": "per unit"},
                    "isInStockAndAvailable": in_stock, "imageUrl": f"https://img.example/{v['spinId']}.jpg",
                    "rating": {"value": "4.3", "count": "1.2k"},
                    "sla": {"value": str(p.eta_min), "unit": "MINS"},
                    "vegClassifier": "VEG_CLASSIFIER_INVALID" if invalid else f"VEG_CLASSIFIER_{p.veg_suffix}",
                    "maxQuantity": sw.max_qty if sw.max_qty is not None else v["max_qty"],
                    "maxQuantityMessage": "Only a few units of this item can be ordered per order.",
                }
            )  # fmt: skip
        products.append(
            {
                "productId": p.id, "parentProductId": p.id, "displayName": f"{p.brand} {p.name}",
                "brand": p.brand, "inStock": True, "isAvail": True, "isPromoted": promoted,
                "badges": badges, "variations": variations,
            }
        )  # fmt: skip
    more = offset + 20 < len(ranked)
    return {"products": products, **({"nextOffset": str(offset + 20)} if more else {})}
