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
        if not searches:
            query = "thali" if veg else "biryani"
            return _tool("search_restaurants", {"query": query}, f"Look for {query} nearby")
        open_rs = [r for r in searches[-1]["result"]["restaurants"] if r["open"]]
        if not open_rs:
            return self._cook(view, results, veg)
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
            for i in menus[-1]["result"]["items"]
            if i["in_stock"]
            and not i["variants"]
            and not i["addons"]
            and (i["veg"] == "veg" or not veg)
            and (budget is None or i["price"] <= budget)
        ]
        if not items:
            return self._cook(view, results, veg)
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

    def _cook(
        self, view: dict[str, Any], results: list[dict[str, Any]], veg: bool
    ) -> dict[str, Any]:
        found = [r for r in results if r["tool"] == "search_products" and "result" in r]
        if not found:
            return _tool(
                "search_products", {"query": "dal"}, "Nothing to order, so look at cooking"
            )
        products = [p for p in found[-1]["result"]["products"] if p["variants"]]
        pick = products[len(view["rejected_plans"]) % len(products)]
        return {
            "action": "propose_plan",
            "args": {
                "path": "cook",
                "reason": f"Nothing suitable to order, so cook with {pick['name']}.",
                "items": [{"id": pick["id"], "variant_id": pick["variants"][0]["id"], "qty": 1}],
                "assumptions": ["Pantry basics at home", "Cooking for one"],
            },
            "rationale": "Order-in not possible",
        }


def _tool(name: str, params: dict[str, Any], why: str) -> dict[str, Any]:
    return {"action": "tool_call", "args": {"name": name, "params": params}, "rationale": why}


def _ask(q: str, field: str, options: list[str]) -> dict[str, Any]:
    return {
        "action": "ask_user",
        "args": {"question": q, "field": field, "options": options},
        "rationale": "Diet is a hard constraint",
    }
