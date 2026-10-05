"""Check the real Swiggy connection, read-only (tasks T6.1, T6.2). Run on your own laptop.

    python scripts/swiggy_check.py

It signs in to Food and Instamart (two browser sign-ins), then makes a handful of READ calls
through the same SwiggyProvider the app uses, and prints counts, timings and field NAMES only.
It never prints address text, phone numbers, names or ids, and calls no write, cart, order,
checkout or payment tool. Paste the output back.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from moodmeals.providers.mcp_connection import ConnectionFailed, McpConnection
from moodmeals.providers.swiggy import SwiggyProvider
from moodmeals.tools.normalise import parse_menu, parse_products, parse_restaurants


def say(msg: str) -> None:
    print(msg, flush=True)


def keys_of(value: Any) -> Any:
    """Key names and value types only."""
    if isinstance(value, dict):
        return {k: type(v).__name__ for k, v in value.items()}
    return type(value).__name__


def timed(label: str, fn):
    t = time.monotonic()
    out = fn()
    say(f"  {label}: {time.monotonic() - t:.1f}s")
    return out


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    conn = McpConnection()
    try:
        for server in ("food", "im"):
            say(f"[1] Signing in to {server} (a browser tab opens)...")
            conn.connect(server)
            say(f"    signed in to {server}")
    except ConnectionFailed as e:
        say(f"FAILED sign-in: {e}")
        return 1
    prov = SwiggyProvider(conn, "dry_run")
    ok = True

    say("[2] Addresses")
    r = timed("list_addresses", lambda: prov.call("list_addresses", {}))
    if not r.ok or not r.data or not r.data.get("addresses"):
        say(f"  FAILED: {r.error}")
        return 1
    items = r.data["addresses"]
    say(f"  count: {len(items)}")
    say(f"  address item field names and types: {keys_of(items[0])}")
    say(f"  top-level keys: {sorted(r.data)}")
    from moodmeals.core.loop import _parse_addresses

    parsed = _parse_addresses(r.data)
    say(f"  parsed with an id: {len(parsed)} of {len(items)}; "
        f"with a label: {sum(1 for a in parsed if a['label'] != 'Saved address')}; "
        f"with text: {sum(1 for a in parsed if a['text'])}")  # fmt: skip
    if not parsed:
        return 1
    aid = parsed[0]["id"]

    say("[3] Food reads")
    r = timed(
        "search_restaurants",
        lambda: prov.call("search_restaurants", {"query": "biryani", "address_id": aid}),
    )
    rs = parse_restaurants(r.data) if r.ok else []
    say(f"  ok={r.ok} restaurants parsed={len(rs)} open={sum(1 for x in rs if x.open)}")
    ok &= bool(rs)
    open_rs = [x for x in rs if x.open] or rs
    if open_rs:
        rid = open_rs[0].id
        r = timed(
            "get_menu", lambda: prov.call("get_menu", {"restaurant_id": rid, "address_id": aid})
        )
        _, items_m = parse_menu(r.data) if r.ok else (None, [])
        stocked = sum(1 for m in items_m if m.in_stock)
        say(f"  ok={r.ok} menu items parsed={len(items_m)} in_stock={stocked}")
        ok &= bool(items_m)
        r = timed(
            "search_dish",
            lambda: prov.call(
                "search_dish",
                {"query": "rice", "restaurant_id": rid, "veg_only": True, "address_id": aid},
            ),
        )
        say(f"  search_dish ok={r.ok}")

    say("[4] Instamart read (does it accept the same address id?)")
    r = timed(
        "search_products", lambda: prov.call("search_products", {"query": "dal", "address_id": aid})
    )
    ps = parse_products(r.data) if r.ok else []
    say(f"  ok={r.ok} products parsed={len(ps)} error={r.error.kind if r.error else None}")
    ok &= bool(ps)

    say("[5] Cart checks (read-only: prints only empty or not empty, never contents)")
    for cart in ("im", "food"):
        params = {"cart": cart, **({"address_id": aid} if cart == "food" else {})}
        r = timed(f"get_cart_state {cart}", lambda p=params: prov.call("get_cart_state", p))
        state = {True: "empty", False: "has items"}.get(r.data.get("empty")) if r.ok else None
        say(f"  {cart} cart: ok={r.ok} state={state} error={r.error.kind if r.error else None}")
        if not r.ok:  # show the reply's key NAMES only, so the parser can be fixed
            raw, args = ("get_cart", {}) if cart == "im" else ("get_food_cart", {"addressId": aid})
            try:
                names = keys_of(conn.call(cart, raw, args, 45))
                say(f"  {cart} cart reply field names and types: {names}")
            except Exception as e:  # noqa: BLE001
                say(f"  {cart} cart raw read failed: {type(e).__name__}")
        ok &= r.ok

    say(f"\nRESULT: {'ALL READS WORKED' if ok else 'SOME READS FAILED (see above)'}")
    say(f"Tools called (normalised names): {sorted(set(prov.calls))}")
    conn.close()
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
