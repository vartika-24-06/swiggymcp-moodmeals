"""Redaction that runs when an event is created (design.md 6 and 12.1; R12, R7.2).

Defence in depth, three layers:
1. Keys: any dict key that names sensitive data has its whole value dropped.
2. Known strings: real address text the code holds in memory for this run is
   replaced wherever it appears (the caller passes it in; it is never stored here).
3. Patterns: phone numbers, emails, long digit runs (pin codes, order ids),
   API-key shapes and "buy again" badges are masked in any free text.

Redaction is lossy on purpose. It must never be the only protection: the model
also never receives this data in the first place (the PII firewall).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

REDACTED = "[redacted]"

# Normalised (lowercase, no separators) key names whose values are always dropped.
_SENSITIVE_KEYS = {
    "address",
    "addresses",
    "addressline",
    "addressline1",
    "addressline2",
    "fulladdress",
    "annotation",
    "landmark",
    "flatno",
    "houseno",
    "pincode",
    "pin",
    "zipcode",
    "phone",
    "phonenumber",
    "mobile",
    "mobilenumber",
    "contact",
    "contactnumber",
    "email",
    "customername",
    "recipient",
    "recipientname",
    "receivername",
    "username",
    "lat",
    "lng",
    "lon",
    "latitude",
    "longitude",
    "orderhistory",
    "pastorders",
    "buyagain",
    "yourgotoitems",
    "gotoitems",
    "accesstoken",
    "refreshtoken",
    "idtoken",
    "apikey",
    "authorization",
    "token",
    "secret",
}

_PHONE = re.compile(r"(?<![\d₹.])\+?\d(?:[\s-]?\d){9,}(?!\d)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_LONG_DIGITS = re.compile(r"(?<!\d)\d{6,}(?!\d)")
_SECRET = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|gsk_[A-Za-z0-9]{16,}"
    r"|sk-ant-[A-Za-z0-9_-]{16,}|Bearer\s+[A-Za-z0-9._~+/=-]{16,})"
)
_BUY_AGAIN = re.compile(
    r"\b(?:buy\s+again|order\s+again|your\s+go[- ]to(?:\s+items?)?)\b", re.IGNORECASE
)


def _norm_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def redact_text(text: str, known_sensitive: Iterable[str] = ()) -> str:
    out = text
    for s in sorted(
        {s for s in known_sensitive if s and len(s.strip()) >= 4}, key=len, reverse=True
    ):
        out = re.sub(re.escape(s.strip()), "<address>", out, flags=re.IGNORECASE)
    out = _SECRET.sub("<secret>", out)
    out = _EMAIL.sub("<email>", out)
    out = _PHONE.sub("<phone>", out)
    out = _LONG_DIGITS.sub("<digits>", out)
    out = _BUY_AGAIN.sub("[badge removed]", out)
    return out


def redact_payload(value: Any, known_sensitive: Iterable[str] = ()) -> Any:
    """Return a redacted copy. Never mutates the input."""
    known = list(known_sensitive)
    if isinstance(value, dict):
        return {
            k: (REDACTED if _norm_key(k) in _SENSITIVE_KEYS else redact_payload(v, known))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_payload(v, known) for v in value]
    if isinstance(value, str):
        return redact_text(value, known)
    if value is None or isinstance(value, bool | int | float):
        return value
    return redact_text(str(value), known)
