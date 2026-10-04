import pytest

from moodmeals.tools.parsing import (
    eta_minutes,
    map_veg_classifier,
    parse_cost_for_two,
    parse_count,
    parse_eta,
    parse_price,
    strip_ad_marker,
)


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("5.1K+", 5100),
        ("1.2L", 120000),
        ("500+", 500),
        ("12", 12),
        ("10K+ ratings", 10000),
        ("1,250", 1250),
        (37, 37),
        (None, None),
        ("no ratings yet", None),
    ],
)
def test_parse_count(text, want):
    assert parse_count(text) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [("₹400 for two", 400), ("₹1,200 for two", 1200), ("cost unknown", None), (None, None)],
)
def test_parse_cost_for_two(text, want):
    assert parse_cost_for_two(text) == want


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("₹1,299", 1299),
        ("Rs. 99", 99),
        (249, 249),
        (99.0, 99),
        ("₹99.50", None),
        (99.5, None),
        (True, None),
        ("free", None),
        (None, None),
    ],
)
def test_parse_price_whole_rupees_only(value, want):
    assert parse_price(value) == want


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("10-20 MINS", (10, 20)),
        ("25 mins", (25, 25)),
        ("1 HR", (60, 60)),
        ("1 HR 10 MINS", (70, 70)),
        ("soon", None),
        (None, None),
        (30, (30, 30)),
    ],
)
def test_parse_eta(text, want):
    assert parse_eta(text) == want


def test_eta_minutes_is_upper_bound():
    assert eta_minutes("10-20 MINS") == 20
    assert eta_minutes("soon") is None


@pytest.mark.parametrize(
    ("value", "want"),
    [
        ("VEG", "veg"),
        (" veg ", "veg"),
        ("NON_VEG", "non_veg"),
        ("Non-Veg", "non_veg"),
        ("EGG", "egg"),
        ("INVALID", "unverified"),
        ("", "unverified"),
        (None, "unverified"),
        (1, "unverified"),
        ("maybe", "unverified"),
        ("VEG_CLASSIFIER_VEG", "veg"),
        ("VEG_CLASSIFIER_INVALID", "unverified"),
        ("VEG_CLASSIFIER_NON_VEG", "non_veg"),
        (True, "veg"),
        (False, "non_veg"),
    ],
)
def test_map_veg_classifier(value, want):
    assert map_veg_classifier(value) == want


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("Sample Dhaba (Ad)", ("Sample Dhaba", True)),
        ("Sample Dhaba (AD)", ("Sample Dhaba", True)),
        ("Sample Dhaba [ad]", ("Sample Dhaba", True)),
        ("Sample Dhaba", ("Sample Dhaba", False)),
        ("Ad Astra Cafe", ("Ad Astra Cafe", False)),
        ("Bread (Adulterated-free)", ("Bread (Adulterated-free)", False)),
    ],
)
def test_strip_ad_marker(name, want):
    assert strip_ad_marker(name) == want
