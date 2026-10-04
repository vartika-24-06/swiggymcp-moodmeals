"""Parsers tested on synthetic payloads in the shapes observed on 2026-10-04."""

import json

from moodmeals.tools.compact import to_text, view_menu_items, view_products, view_restaurants
from moodmeals.tools.normalise import (
    parse_dish_search,
    parse_menu,
    parse_products,
    parse_restaurants,
)

RESTAURANTS = {
    "restaurants": [
        {
            "id": "9001",
            "name": "Sample Dhaba (Ad)",
            "cuisines": ["North Indian", "Thalis"],
            "avgRating": 4.3,
            "totalRatings": "5.1K+",
            "costForTwo": "₹400 for two",
            "areaName": "Demo Area",
            "distanceKm": 2.6,
            "deliveryTimeMinutes": 22,
            "deliveryTimeRange": "20-25 MINS",
            "imageUrl": "https://img.example/a.jpg",
            "availabilityStatus": "OPEN",
            "veg": True,
        },
        {
            "id": "9002",
            "name": "Demo Kitchen",
            "cuisines": ["Chinese"],
            "avgRating": 3.9,
            "totalRatings": "230",
            "costForTwo": "₹250 for two",
            "distanceKm": 4.1,
            "deliveryTimeMinutes": 35,
            "availabilityStatus": "CLOSED",
        },
        {"name": "No id", "cuisines": []},
    ],
    "dishes": [],
    "total": 3,
    "hasMore": True,
    "nextOffset": 10,
}

MENU = {
    "restaurant": {
        "id": "9001",
        "name": "Sample Dhaba",
        "city": "Demo City",
        "areaName": "X",
        "address": "Demo address",
        "cuisines": ["North Indian"],
        "avgRating": 4.3,
        "totalRatingsString": "5.1K+ ratings",
        "costForTwoMessage": "₹400 for two",
        "isOpen": True,
        "deliveryTime": 22,
        "slaString": "20-25 MINS",
    },
    "categories": [
        {
            "title": "Mains",
            "categoryId": "c1",
            "items": [
                {
                    "id": "i1",
                    "name": "Moong Khichdi",
                    "price": 150,
                    "inStock": 1,
                    "isVeg": True,
                    "hasVariants": False,
                    "hasAddons": True,
                    "description": "long text",
                    "imageUrl": "u",
                },
                {"id": "i2", "name": "Chicken Curry", "price": 320, "inStock": 0, "isVeg": False},
                {"id": "i3", "name": "Broken price", "price": "free", "isVeg": True},
            ],
            "subcategories": [
                {
                    "title": "Nested",
                    "items": [{"id": "i4", "name": "Raita", "price": 40, "isVeg": True}],
                }
            ],
        },
    ],
}

DISH_SEARCH = {
    "items": [
        {
            "menu_item_id": "d1",
            "name": "Plain Khichdi",
            "price": 120,
            "inStock": 1,
            "isVeg": True,
            "hasVariants": False,
            "hasAddons": False,
            "rating": "4.2",
            "imageUrl": "u",
        },
    ],
    "restaurantIdOfAddedItem": "9001",
}

PRODUCTS = {
    "products": [
        {
            "productId": "p1",
            "displayName": "Moong Dal",
            "brand": "Sample Foods",
            "isPromoted": False,
            "badges": [{"type": "BADGE_TYPE_TRENDING", "text": "TRENDING"}],
            "variations": [
                {
                    "spinId": "s1",
                    "quantityDescription": "500 g",
                    "isInStockAndAvailable": True,
                    "price": {"mrp": 70, "offerPrice": 60, "unitLevelPrice": "120/kg"},
                    "vegClassifier": "VEG_CLASSIFIER_VEG",
                    "maxQuantity": 3,
                    "sla": {"value": "12", "unit": "MINS"},
                },
                {
                    "spinId": "s2",
                    "quantityDescription": "1 kg",
                    "isInStockAndAvailable": False,
                    "price": {"mrp": 130, "offerPrice": 115},
                    "vegClassifier": "VEG_CLASSIFIER_VEG",
                    "maxQuantity": 2,
                },
            ],
        },
        {
            "productId": "p2",
            "displayName": "Mystery Paste",
            "isPromoted": True,
            "badges": [{"type": "BADGE_TYPE_AD", "text": "AD"}, {"type": "X", "text": "BUY AGAIN"}],
            "variations": [
                {
                    "spinId": "s3",
                    "quantityDescription": "100 g",
                    "isInStockAndAvailable": True,
                    "price": {"mrp": 50, "offerPrice": 50},
                    "vegClassifier": "VEG_CLASSIFIER_INVALID",
                    "maxQuantity": 2,
                },
            ],
        },
        {
            "productId": "p3",
            "displayName": "Out of stock",
            "variations": [
                {
                    "spinId": "s4",
                    "isInStockAndAvailable": False,
                    "price": {"mrp": 1, "offerPrice": 1},
                    "maxQuantity": 1,
                }
            ],
        },
    ]
}


def test_parse_restaurants():
    rs = parse_restaurants(RESTAURANTS)
    assert [r.id for r in rs] == ["9001", "9002"]  # the one without an id is dropped
    a, b = rs
    assert (a.name, a.sponsored) == ("Sample Dhaba", True)
    assert (a.rating_count, a.cost_for_two, a.eta_minutes, a.open) == (5100, 400, 22, True)
    assert b.open is False and b.sponsored is False


def test_parse_menu_walks_nested_categories_and_drops_unreadable_prices():
    restaurant, items = parse_menu(MENU)
    assert restaurant and restaurant.open and restaurant.rating_count == 5100
    assert [m.id for m in items] == ["i1", "i2", "i4"]  # i3 has an unreadable price
    by_id = {m.id: m for m in items}
    assert by_id["i1"].veg == "veg" and by_id["i1"].in_stock and by_id["i1"].has_addons
    assert by_id["i2"].veg == "non_veg" and not by_id["i2"].in_stock
    assert all(m.restaurant_id == "9001" for m in items)


def test_parse_dish_search_uses_the_scoped_restaurant():
    [dish] = parse_dish_search(DISH_SEARCH, "9001")
    assert (dish.id, dish.restaurant_id, dish.price, dish.veg) == ("d1", "9001", 120, "veg")


def test_parse_products():
    ps = parse_products(PRODUCTS)
    assert [p.id for p in ps] == ["p1", "p2"]  # p3 has nothing in stock
    p1, p2 = ps
    assert [v.spin_id for v in p1.variants] == ["s1"]  # the out-of-stock pack is dropped
    assert (p1.veg, p1.sponsored, p1.eta_minutes) == ("veg", False, 12)
    assert p1.variants[0].price == 60 and p1.variants[0].max_qty == 3
    assert p2.veg == "unverified" and p2.sponsored is True


def test_compact_view_keeps_only_whitelisted_fields_and_is_smaller():
    raw = json.dumps(PRODUCTS)
    view = to_text(view_products(parse_products(PRODUCTS)))
    assert len(view) < len(raw)
    for leaked in ("BUY AGAIN", "badges", "unitLevelPrice", "img", "BADGE_TYPE"):
        assert leaked not in view
    rview = to_text(view_restaurants(parse_restaurants(RESTAURANTS)))
    assert "Demo Area" not in rview and "imageUrl" not in rview and "areaName" not in rview
    mview = to_text(view_menu_items(parse_menu(MENU)[1]))
    assert "description" not in mview and "long text" not in mview
