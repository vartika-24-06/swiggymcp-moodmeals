"""A rule-based stand-in for a model: the "scripted demo" that needs no key.

It reads only the model view, like a real model would, so the whole app can be shown,
tested and recorded with no API cost. It is deliberately simple and says so in the UI.
"""

from __future__ import annotations

from typing import Any


class DemoLLM:
    provider = "demo"
    model = "scripted-demo"

    def __init__(self) -> None:
        self._calls = 0

    def usage(self):  # same shape as the real adapters
        from moodmeals.models.adapters import Usage

        return Usage(calls=self._calls)

    def cost_estimate(self) -> float:
        return 0.0

    def next_action(self, view: dict[str, Any]) -> dict[str, Any]:
        self._calls += 1
        results = view["untrusted_data"]["tool_results"]
        veg = view["hard_constraints"]["vegetarian"] or any(
            "veg" in a["a"].lower() and "non" not in a["a"].lower() for a in view["answers"]
        )
        asked_diet = any(
            "diet" in a["q"].lower() or "veg" in a["q"].lower() for a in view["answers"]
        )
        if not veg and not asked_diet and view["limits_left"]["questions"] > 0:
            return _ask(
                "Veg, non-veg, or no preference today?", "diet", ["Veg", "Non-veg", "No preference"]
            )

        searches = [r for r in results if r["tool"] == "search_restaurants" and "result" in r]
        if not searches and any(r["tool"] == "search_restaurants" for r in results):
            return self._quick_meal(view, results, "Restaurant search is not working right now")
        if not searches:
            query = "thali" if veg else "biryani"
            return _tool("search_restaurants", {"query": query}, f"Look for {query} nearby")
        open_rs = [r for r in searches[-1]["result"].get("restaurants", []) if r["open"]]
        if not open_rs:
            return self._quick_meal(view, results, "Every restaurant is closed right now")
        restaurant = open_rs[0]
        menus = [r for r in results if r["tool"] == "get_menu" and "result" in r]
        if not menus:
            return _tool(
                "get_menu",
                {"restaurant_id": restaurant["id"]},
                f"See what {restaurant['name']} has",
            )
        budget = view["hard_constraints"]["budget_inr"]
        items = [
            i
            for i in menus[-1]["result"].get("items", [])
            if i["in_stock"]
            and not i["variants"]
            and not i["addons"]
            and (i["veg"] == "veg" or not veg)
            and (budget is None or i["price"] <= budget)
        ]
        if not items:
            return self._quick_meal(view, results, "Nothing at the restaurant fits")
        items.sort(key=lambda i: -i["price"])  # mains cost more than sides
        pick = items[len(view["rejected_plans"]) % len(items)]
        return {
            "action": "propose_plan",
            "args": {
                "path": "order_in",
                "reason": f"{pick['name']} from {restaurant['name']} is open and quick.",
                "restaurant_id": restaurant["id"],
                "items": [{"id": pick["id"], "qty": 1}],
                "assumptions": [
                    "Eating alone",
                    "Budget not stated" if budget is None else "Within budget",
                ],
            },
            "rationale": "Open restaurant, item in stock",
        }

    def _quick_meal(
        self, view: dict[str, Any], results: list[dict[str, Any]], why: str
    ) -> dict[str, Any]:
        """Ordering in is not possible: offer a ready-to-eat or quick-cook Instamart meal,
        or stop with the reason (requirements R4.3)."""
        veg = view["hard_constraints"]["vegetarian"]
        budget = view["hard_constraints"]["budget_inr"]
        searched = [r["params"].get("query") for r in results if r["tool"] == "search_products"]
        quick = []
        for r in results:
            if r["tool"] == "search_products" and "result" in r:
                for p in r["result"].get("products", []):
                    cheap = [v for v in p["variants"] if budget is None or v["price"] <= budget]
                    if _is_quick(p["name"]) and cheap and (p["veg"] == "veg" or not veg):
                        quick.append((p, cheap[0]))
        if not quick:
            for query in ("instant", "ready"):
                if query not in searched:
                    return _tool(
                        "search_products", {"query": query}, f"{why}; look for quick meals"
                    )
            return {
                "action": "stop_search",
                "args": {
                    "reason": f"{why}, and I found no ready-to-eat or quick-cook meal to offer."
                },
                "rationale": "Neither path has anything usable",
            }
        pick, variant = quick[len(view["rejected_plans"]) % len(quick)]
        return {
            "action": "propose_plan",
            "args": {
                "path": "cook",
                "reason": f"{why}, so here is a quick meal from Instamart: {pick['name']}.",
                "items": [{"id": pick["id"], "variant_id": variant["id"], "qty": 1}],
                "assumptions": ["Eating alone", "Needs little or no cooking"],
            },
            "rationale": "Order-in not possible; offering a quick meal",
        }


def _is_quick(name: str) -> bool:
    low = name.lower()
    return "instant" in low or "ready" in low


def _tool(name: str, params: dict[str, Any], why: str) -> dict[str, Any]:
    return {"action": "tool_call", "args": {"name": name, "params": params}, "rationale": why}


def _ask(q: str, field: str, options: list[str]) -> dict[str, Any]:
    return {
        "action": "ask_user",
        "args": {"question": q, "field": field, "options": options},
        "rationale": "Diet is a hard constraint",
    }
