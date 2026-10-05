"""The `fixed_workflow` baseline (design 13.2): no model, no path choice.

Always: order in, search, pick the best open restaurant by rating, read its menu, take one
in-budget item. It never looks at Instamart and never adapts, so it shows what path choice
and replanning add. It speaks the same one-action-per-turn contract as a model.
"""

from __future__ import annotations

from typing import Any


class FixedWorkflowLLM:
    provider = "fixed"
    model = "fixed-workflow"

    def __init__(self) -> None:
        self._calls = 0

    def usage(self):
        from moodmeals.models.adapters import Usage

        return Usage(calls=self._calls)

    def cost_estimate(self) -> float:
        return 0.0

    def next_action(self, view: dict[str, Any]) -> dict[str, Any]:
        self._calls += 1
        results = view["untrusted_data"]["tool_results"]
        hc = view["hard_constraints"]
        veg, budget = hc["vegetarian"], hc["budget_inr"]

        searches = [r for r in results if r["tool"] == "search_restaurants"]
        if not searches:
            query = "thali" if veg else "biryani"
            return _tool("search_restaurants", {"query": query})
        ok = [r for r in searches if "result" in r]
        if not ok:
            return _stop("Restaurant search failed.")
        open_rs = sorted(
            (r for r in ok[-1]["result"]["restaurants"] if r["open"]),
            key=lambda r: -(r["rating"] or 0),
        )
        if not open_rs:
            return _stop("No restaurant is open right now.")
        pick = open_rs[0]
        menus = [r for r in results if r["tool"] == "get_menu" and "result" in r]
        if not menus:
            return _tool("get_menu", {"restaurant_id": pick["id"]})
        items = [
            i
            for i in menus[-1]["result"]["items"]
            if i["in_stock"]
            and not i["variants"]
            and (i["veg"] == "veg" or not veg)
            and (budget is None or i["price"] <= budget)
        ]
        if not items:
            return _stop("Nothing on the menu fits.")
        items.sort(key=lambda i: -i["price"])
        item = items[len(view["rejected_plans"]) % len(items)]
        return {
            "action": "propose_plan",
            "args": {
                "path": "order_in",
                "reason": f"{item['name']} from {pick['name']}.",
                "restaurant_id": pick["id"],
                "items": [{"id": item["id"], "qty": 1}],
                "assumptions": ["Eating alone"],
            },
            "rationale": "Fixed rule: best open restaurant, one in-budget item",
        }


def _tool(name: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"action": "tool_call", "args": {"name": name, "params": params}, "rationale": "fixed"}


def _stop(reason: str) -> dict[str, Any]:
    return {"action": "stop_search", "args": {"reason": reason}, "rationale": "fixed"}
