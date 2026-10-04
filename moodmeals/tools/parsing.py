"""Small pure parsers for Swiggy's text-formatted values (design.md 5.3).

Rules: parse in code, never guess. A value that cannot be read returns `None`
(or `"unverified"` for veg), so callers can flag it instead of trusting a
silent guess (R5.5, R7.4, R7.7).
"""

from __future__ import annotations

import re

from moodmeals.models.types import Veg

_NUM = r"\d[\d,]*(?:\.\d+)?"
_MULTIPLIER = {"k": 1_000, "l": 100_000, "lakh": 100_000, "m": 1_000_000, "cr": 10_000_000}


def parse_count(text: str | int | None) -> int | None:
    """'5.1K+' -> 5100, '1.2L' -> 120000, '500+' -> 500, '12' -> 12.

    A trailing '+' means "at least"; we return the lower bound.
    """
    if text is None:
        return None
    if isinstance(text, int) and not isinstance(text, bool):
        return text
    m = re.search(rf"({_NUM})\s*(lakh|cr|k|l|m)?\b", str(text).strip().lower())
    if not m:
        return None
    number = float(m.group(1).replace(",", ""))
    value = number * _MULTIPLIER.get(m.group(2) or "", 1)
    return round(value)


def parse_price(value: str | int | float | None) -> int | None:
    """'₹1,299' -> 1299, 'Rs. 99' -> 99, 249 -> 249.

    Whole rupees only. A price with a fractional part returns None, because
    rounding money silently is how totals go wrong; the validator treats None
    as "price could not be read".
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    m = re.search(_NUM, str(value))
    if not m:
        return None
    number = float(m.group(0).replace(",", ""))
    return int(number) if number.is_integer() else None


def parse_cost_for_two(text: str | int | None) -> int | None:
    """'₹400 for two' -> 400, '₹1,200 for two' -> 1200."""
    return parse_price(text)


def parse_eta(text: str | int | None) -> tuple[int, int] | None:
    """'10-20 MINS' -> (10, 20), '25 mins' -> (25, 25), '1 HR' -> (60, 60).

    Returns (low, high) in minutes. Use `eta_minutes` for one conservative number.
    """
    if text is None:
        return None
    if isinstance(text, int) and not isinstance(text, bool):
        return (text, text)
    s = str(text).strip().lower()
    hours = re.search(r"(\d+)\s*(?:hr|hrs|hour|hours)\b", s)
    mins = re.search(r"(\d+)(?:\s*-\s*(\d+))?\s*(?:min|mins|minutes)\b", s)
    if hours:
        base = int(hours.group(1)) * 60
        if mins:
            lo = base + int(mins.group(1))
            hi = base + int(mins.group(2) or mins.group(1))
            return (lo, hi)
        return (base, base)
    if mins:
        lo = int(mins.group(1))
        return (lo, int(mins.group(2) or lo))
    return None


def eta_minutes(text: str | int | None) -> int | None:
    """One conservative number: the upper end of the range."""
    parsed = parse_eta(text)
    return parsed[1] if parsed else None


# Only clearly named classifiers map to a real class. Everything else, including
# empty, numeric codes, "INVALID" and unknown strings, is "unverified" (R7.4).
# The exact strings Swiggy sends are to be confirmed against recorded fixtures.
_VEG = {"veg", "vegetarian", "pure_veg", "pure veg"}
_EGG = {"egg", "egg_veg", "contains_egg"}
_NON_VEG = {"non_veg", "non-veg", "nonveg", "non veg", "non_vegetarian", "non-vegetarian"}


def map_veg_classifier(value: str | None) -> Veg:
    if not isinstance(value, str):
        return "unverified"
    key = value.strip().lower()
    if key in _VEG:
        return "veg"
    if key in _EGG:
        return "egg"
    if key in _NON_VEG:
        return "non_veg"
    return "unverified"


_AD = re.compile(r"\s*[\(\[]\s*ad\s*[\)\]]\s*$", re.IGNORECASE)


def strip_ad_marker(name: str) -> tuple[str, bool]:
    """'Sample Dhaba (Ad)' -> ('Sample Dhaba', True). Only a trailing marker counts."""
    cleaned, n = _AD.subn("", name)
    return cleaned.strip(), n > 0
