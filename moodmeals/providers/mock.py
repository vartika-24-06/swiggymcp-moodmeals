"""MockProvider: the seeded synthetic world behind `ActionProvider` (tasks T2.3; design 7.1).

Deterministic: the same seed and switches give the same responses. Writes are
simulated in memory only and never leave the process. The model never calls this
directly; writes are reached only through `WriteGate`.
"""

from __future__ import annotations

from typing import Any

from moodmeals.providers import world as w
from moodmeals.providers.base import Mode, ToolError, ToolResult
from moodmeals.providers.switches import Switches

READ_TOOLS = {
    "list_addresses", "search_restaurants", "get_menu", "search_dish", "search_products",
    "get_cart_state",
}  # fmt: skip
WRITE_TOOLS = {"update_food_cart", "update_cart", "place_food_order", "checkout"}


class MockProvider:
    mode: Mode = "mock"

    def __init__(self, seed: int = 0, switches: Switches | None = None, n_addresses: int = 1):
        self.seed = seed
        self.switches = switches or Switches()
        self.world = w.build_world(seed, n_addresses)
        self.calls: list[str] = []  # tool names, in order
        self.food_cart: dict[str, Any] | None = None  # {"restaurant_id", "items"}
        self.im_cart: list[dict[str, Any]] = []
        self.sim_orders: list[dict[str, Any]] = []

    # ------------------------------------------------------------------ #

    def call(self, tool: str, params: dict[str, Any]) -> ToolResult:
        self.calls.append(tool)
        latency = 300 + (hash((self.seed, len(self.calls), tool)) % 900)
        if tool not in READ_TOOLS | WRITE_TOOLS:
            return self._err("unknown_tool", f"Unknown tool: {tool}", latency)
        failure = self.switches.fail_tools.get(tool)
        if failure == "timeout":
            return self._err("timeout", f"{tool} timed out", 15000)
        if failure:
            return self._err("error", f"{tool} failed", latency)
        try:
            data = self._dispatch(tool, params)
        except ValueError as e:
            return self._err("bad_params", str(e), latency)
        if data is None:
            return self._err("not_found", "No such restaurant", latency)
        return ToolResult(True, data, None, latency)

    @staticmethod
    def _err(kind: Any, message: str, latency: int) -> ToolResult:
        return ToolResult(False, None, ToolError(kind, message), latency)

    # ------------------------------------------------------------------ #

    def _dispatch(self, tool: str, p: dict[str, Any]) -> dict[str, Any] | None:
        sw, world = self.switches, self.world
        if tool == "list_addresses":
            return w.addresses_payload(world, _int(p, "page", 1), 10)
        if tool == "get_cart_state":
            return {"empty": self.food_cart is None and not self.im_cart}
        if tool == "search_restaurants":
            return w.search_restaurants_payload(world, sw, _query(p), _int(p, "offset", 0))
        if tool == "get_menu":
            return w.menu_payload(
                world, sw, _str(p, "restaurant_id"), _int(p, "page", 1), _int(p, "page_size", 5)
            )
        if tool == "search_dish":
            return w.dish_search_payload(
                world, sw, _query(p), _str(p, "restaurant_id"), bool(p.get("veg_only")),
                _int(p, "offset", 0),
            )  # fmt: skip
        if tool == "search_products":
            return w.search_products_payload(world, sw, _query(p), _int(p, "offset", 0))
        return self._write(tool, p)

    def _write(self, tool: str, p: dict[str, Any]) -> dict[str, Any]:
        """Simulated writes. Instamart `update_cart` replaces the whole cart (A7)."""
        if tool == "update_cart":
            self.im_cart = _items(p)
            return {"simulated": True, "cart_items": len(self.im_cart), "replaced_cart": True}
        if tool == "update_food_cart":
            self.food_cart = {"restaurant_id": _str(p, "restaurant_id"), "items": _items(p)}
            return {"simulated": True, "cart_items": len(self.food_cart["items"])}
        if tool == "place_food_order":
            if not self.food_cart:
                raise ValueError("The food cart is empty")
            order = {"kind": "food", **self.food_cart}
            self.food_cart = None
        else:  # checkout
            if not self.im_cart:
                raise ValueError("The cart is empty")
            order = {"kind": "instamart", "items": self.im_cart}
            self.im_cart = []
        self.sim_orders.append(order)
        return {"simulated": True, "order_number": len(self.sim_orders)}


def _query(p: dict[str, Any]) -> str:
    q = p.get("query")
    if not isinstance(q, str) or not q.strip():
        raise ValueError("query must be a non-empty string")
    if len(q) > 80:
        raise ValueError("query is too long")
    return q.strip()


def _str(p: dict[str, Any], key: str) -> str:
    v = p.get(key)
    if not isinstance(v, str | int) or isinstance(v, bool) or not str(v).strip():
        raise ValueError(f"{key} is required")
    return str(v)


def _int(p: dict[str, Any], key: str, default: int) -> int:
    v = p.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return v


def _items(p: dict[str, Any]) -> list[dict[str, Any]]:
    items = p.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("items must be a non-empty list")
    return items
