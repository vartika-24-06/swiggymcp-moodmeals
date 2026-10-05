"""Spike C, part 2 (T0.4): measure compaction on your LOCAL raw captures.

Reads `spikes/captures/<server>/<tool>.json` (written by capture_shapes.py, git-ignored),
parses each with the real parsers, builds the model view, and prints ONLY numbers:
size before and after, items parsed versus items in the raw response, and counts of
veg and sponsored values. It prints no names, ids or text. Nothing is written.

Run:  python spikes/compact_check.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from moodmeals.tools.compact import (  # noqa: E402
    to_text,
    view_menu_items,
    view_products,
    view_restaurants,
)
from moodmeals.tools.normalise import (  # noqa: E402
    parse_dish_search,
    parse_menu,
    parse_products,
    parse_restaurants,
)

CAP = Path("spikes/captures")


def load(server: str, tool: str) -> dict[str, Any] | None:
    path = CAP / server / f"{tool}.json"
    if not path.exists():
        return None
    payloads = json.loads(path.read_text(encoding="utf-8"))
    return payloads[0] if payloads else None  # copies of one response; take the first


def raw_tokens(payload: dict[str, Any]) -> tuple[int, int]:
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return len(text), len(text) // 4


def count_menu_items(cats: list[dict[str, Any]]) -> int:
    n = 0
    for c in cats or []:
        n += len(c.get("items") or []) + count_menu_items(c.get("subcategories") or [])
    return n


def report(
    label: str, payload: dict[str, Any], view: list[dict[str, Any]], parsed: int, raw_n: int
):
    rc, rt = raw_tokens(payload)
    text = to_text(view)
    cc, ct = len(text), len(text) // 4
    saved = f"{100 - round(100 * ct / rt)}%" if rt else "-"
    print(
        f"{label:22} raw {rc:>6} chars ~{rt:>5} tok | compact {cc:>5} chars ~{ct:>5} tok | "
        f"saved {saved:>4} | parsed {parsed}/{raw_n}"
    )


def main() -> int:
    if not CAP.exists():
        print("No spikes/captures folder. Run capture_shapes.py first.")
        return 1
    print("Token numbers are characters / 4: a rough estimate, not a tokenizer.\n")

    if p := load("food", "search_restaurants"):
        rs = parse_restaurants(p)
        report(
            "search_restaurants", p, view_restaurants(rs), len(rs), len(p.get("restaurants") or [])
        )
        print(
            f"   open {sum(r.open for r in rs)}/{len(rs)}, "
            f"sponsored {sum(r.sponsored for r in rs)}/{len(rs)}"
        )
    if p := load("food", "get_restaurant_menu"):
        restaurant, items = parse_menu(p)
        report(
            "get_restaurant_menu",
            p,
            view_menu_items(items),
            len(items),
            count_menu_items(p.get("categories") or []),
        )
        print(
            f"   veg values: {dict(Counter(m.veg for m in items))}, "
            f"in stock {sum(m.in_stock for m in items)}/{len(items)}, "
            f"restaurant parsed: {restaurant is not None}"
        )
    if p := load("food", "search_menu"):
        items = parse_dish_search(p, "scoped")
        report("search_menu", p, view_menu_items(items), len(items), len(p.get("items") or []))
        print(f"   veg values: {dict(Counter(m.veg for m in items))}")
    if p := load("im", "search_products"):
        ps = parse_products(p)
        report("search_products", p, view_products(ps), len(ps), len(p.get("products") or []))
        print(
            f"   veg values: {dict(Counter(x.veg for x in ps))}, "
            f"sponsored {sum(x.sponsored for x in ps)}/{len(ps)}, "
            f"variants per product {sorted(Counter(len(x.variants) for x in ps).items())}"
        )
    print(
        "\n'parsed a/b' below b means items were dropped (missing id, unreadable price,"
        " nothing in stock). That is expected for some, but tell me the numbers."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
