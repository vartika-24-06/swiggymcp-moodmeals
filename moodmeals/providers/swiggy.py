"""SwiggyProvider: real read-only Swiggy data behind `ActionProvider` (tasks T6.1; design 7.3).

Translates the normalised tool names and parameters (design 5.1) to Swiggy's, over a
connection object (`McpConnection`, or a fake in tests). Safety by construction:

- An allowlist of Swiggy READ tools. Anything else is refused before it reaches the network,
  including every order, checkout, address-changing and payment tool.
- Two cart-update WRITE tools exist (T6.3), and only in live mode: `update_food_cart` and
  `update_cart`. In dry-run they are refused here too, and the `WriteGate` never reaches the
  provider at all. Placing an order (`place_food_order`, `checkout`) is not implemented: v1
  never calls a payment tool (R10.7), so a person places the order in the Swiggy app.
- `get_cart_state` reads the Instamart or Food cart and returns ONLY whether it is empty.
  Cart contents never leave this module (DQ6).
- Errors carry a kind, never the server's response text.
"""

from __future__ import annotations

import time
from typing import Any, Literal, Protocol

from moodmeals.providers.base import Mode, ToolError, ToolResult

# Swiggy tool -> server. READ tools only: this is the whole list of what can be called.
READ_ALLOWLIST: dict[str, str] = {
    "get_addresses": "food",
    "search_restaurants": "food",
    "get_restaurant_menu": "food",
    "search_menu": "food",
    "search_products": "im",
}
# Cart reads (read tools), kept apart from READ_ALLOWLIST: only the emptiness leaves the provider.
CART_READ: dict[str, str] = {"im": "get_cart", "food": "get_food_cart"}
# Live-only writes: cart updates, never orders. This is the whole list of what can be written.
WRITE_ALLOWLIST: dict[str, str] = {"update_food_cart": "food", "update_cart": "im"}
MAX_CART_LINES = 20
ADDRESS_PAGE_SIZE = 10  # the tool's documented maximum
MAX_ADDRESS_PAGES = 3
READ_TIMEOUT_S = 45.0  # one real call took about 16 s in Spike A
WRITE_TIMEOUT_S = 45.0  # a timed-out write is reported, never retried


class Connection(Protocol):
    def call(
        self, server: str, tool: str, args: dict[str, Any], timeout: float
    ) -> dict[str, Any]: ...


class SwiggyProvider:
    def __init__(self, connection: Connection, mode: Literal["dry_run", "live"] = "dry_run"):
        self._conn = connection
        self.mode: Mode = mode
        self.calls: list[str] = []  # normalised names, in order

    def call(self, tool: str, params: dict[str, Any]) -> ToolResult:
        self.calls.append(tool)
        started = time.monotonic()
        try:
            if tool == "list_addresses":
                data = self._addresses()
            elif tool == "get_cart_state":
                data = self._cart_state(params)
            elif tool in ("update_food_cart", "update_cart"):
                if self.mode != "live":  # fail closed: writes exist only in live mode
                    raise _Refused(f"{tool} is not available in this mode")
                swiggy_tool, args = self._translate_write(tool, params)
                data = self._write(swiggy_tool, args)
            else:
                swiggy_tool, args = self._translate(tool, params)
                data = self._read(swiggy_tool, args)
        except _Refused as e:
            return _err("unknown_tool", str(e), started)
        except _BadParams as e:
            return _err("bad_params", str(e), started)
        except TimeoutError:
            return _err("timeout", f"{tool} timed out", started)
        except Exception as e:  # noqa: BLE001  (never echo the server's text)
            return _err("error", f"{tool} failed ({type(e).__name__})", started)
        return ToolResult(True, data, None, int((time.monotonic() - started) * 1000))

    # ------------------------------------------------------------------ #

    def _read(self, swiggy_tool: str, args: dict[str, Any]) -> dict[str, Any]:
        server = READ_ALLOWLIST.get(swiggy_tool)
        if server is None:  # fail closed
            raise _Refused(f"{swiggy_tool} is not an allowed read tool")
        try:
            return self._conn.call(server, swiggy_tool, args, READ_TIMEOUT_S)
        except Exception as e:  # CallTimeout from the connection means a timeout
            if type(e).__name__ == "CallTimeout":
                raise TimeoutError from None
            raise

    def _write(self, swiggy_tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """One cart update. The reply is dropped: it can carry the address and the bill."""
        server = WRITE_ALLOWLIST.get(swiggy_tool)
        if server is None or self.mode != "live":  # fail closed
            raise _Refused(f"{swiggy_tool} is not an allowed write tool")
        try:
            self._conn.call(server, swiggy_tool, args, WRITE_TIMEOUT_S)
        except Exception as e:
            if type(e).__name__ == "CallTimeout":
                raise TimeoutError from None
            raise
        return {"updated": True}

    def _cart_state(self, p: dict[str, Any]) -> dict[str, Any]:
        """Whether a cart is empty ("cart": "im" or "food"). Anything unrecognised is an error,
        so a caller can never mistake "could not tell" for "empty"."""
        which = p.get("cart")
        if which not in CART_READ:
            raise _BadParams("cart must be im or food")
        args = {"addressId": _id(p, "address_id")} if which == "food" else {}
        try:
            cart = self._conn.call(which, CART_READ[which], args, READ_TIMEOUT_S)
        except Exception as e:
            if type(e).__name__ == "CallTimeout":
                raise TimeoutError from None
            raise
        items = _cart_items(cart)
        if items is None:
            raise ValueError("unrecognised cart shape")
        return {"empty": len(items) == 0}

    def _addresses(self) -> dict[str, Any]:
        """All saved addresses, page by page (an account can have more than one page)."""
        merged: dict[str, Any] = {}
        items: list[Any] = []
        for page in range(1, MAX_ADDRESS_PAGES + 1):
            data = self._read("get_addresses", {"page": page, "pageSize": ADDRESS_PAGE_SIZE})
            merged = merged or data
            items.extend(data.get("addresses") or [])
            if not (data.get("pagination") or {}).get("hasMore"):
                break
        return {**merged, "addresses": items, "total": len(items)}

    @staticmethod
    def _translate(tool: str, p: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        addr = {"addressId": p["address_id"]} if p.get("address_id") else {}
        if tool == "search_restaurants":
            return "search_restaurants", {"query": _query(p), "offset": _int(p, "offset"), **addr}
        if tool == "get_menu":
            args: dict[str, Any] = {"restaurantId": _id(p, "restaurant_id"), **addr}
            if "page" in p:
                args["page"] = _int(p, "page", 1)
            if "page_size" in p:
                args["pageSize"] = min(8, max(1, _int(p, "page_size", 5)))  # tool max is 8
            return "get_restaurant_menu", args
        if tool == "search_dish":
            return "search_menu", {
                "query": _query(p),
                "restaurantIdOfAddedItem": _id(p, "restaurant_id"),
                "vegFilter": 1 if p.get("veg_only") else 0,
                "offset": _int(p, "offset"),
                **addr,
            }
        if tool == "search_products":
            return "search_products", {"query": _query(p), "offset": _int(p, "offset"), **addr}
        raise _Refused(f"{tool} is not available in this mode")

    @staticmethod
    def _translate_write(tool: str, p: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        address = _id(p, "address_id")
        items = p.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_CART_LINES:
            raise _BadParams(f"items must hold 1 to {MAX_CART_LINES} lines")
        if tool == "update_food_cart":
            return "update_food_cart", {
                "restaurantId": _id(p, "restaurant_id"),
                "addressId": address,
                "cartItems": [{"menu_item_id": _id(i, "id"), "quantity": _qty(i)} for i in items],
            }
        return "update_cart", {  # Instamart: replaces the whole cart (DQ6)
            "selectedAddressId": address,
            "items": [{"spinId": _id(i, "spin_id"), "quantity": _qty(i)} for i in items],
        }


class _Refused(Exception):
    pass


class _BadParams(Exception):
    pass


def _err(kind: Any, message: str, started: float) -> ToolResult:
    return ToolResult(
        False, None, ToolError(kind, message), int((time.monotonic() - started) * 1000)
    )


def _query(p: dict[str, Any]) -> str:
    q = p.get("query")
    if not isinstance(q, str) or not q.strip():
        raise _BadParams("query must be a non-empty string")
    return q.strip()[:80]


def _id(p: dict[str, Any], key: str) -> str:
    v = p.get(key)
    if v is None or isinstance(v, bool) or not str(v).strip():
        raise _BadParams(f"{key} is required")
    return str(v)


def _qty(item: Any) -> int:
    q = item.get("qty") if isinstance(item, dict) else None
    if not isinstance(q, int) or isinstance(q, bool) or not 1 <= q <= 20:
        raise _BadParams("qty must be a whole number from 1 to 20")
    return q


def _cart_items(cart: Any) -> list[Any] | None:
    """The line list of a cart reply, or None if the shape is not recognised. Observed
    2026-10-05: Instamart's reply has a top-level `items` list; Food's empty cart is an
    envelope with statusCode 0, successful true and data null. A Food cart WITH items has not
    been seen, so a few likely places are tried; anything else stays unrecognised and blocks
    a write."""
    if not isinstance(cart, dict):
        return None
    if (
        cart.get("successful") is True
        and cart.get("statusCode") == 0
        and ("data" in cart and cart["data"] is None)
    ):
        return []  # Food: the observed empty-cart reply
    for holder in (cart, cart.get("cart"), cart.get("data")):
        if isinstance(holder, dict):
            for key in ("items", "cartItems"):
                if isinstance(holder.get(key), list):
                    return holder[key]
    return None


def _int(p: dict[str, Any], key: str, default: int = 0) -> int:
    v = p.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise _BadParams(f"{key} must be a non-negative integer")
    return v
