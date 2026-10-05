"""SwiggyProvider: real read-only Swiggy data behind `ActionProvider` (tasks T6.1; design 7.3).

Translates the normalised tool names and parameters (design 5.1) to Swiggy's, over a
connection object (`McpConnection`, or a fake in tests). Safety by construction:

- An allowlist of Swiggy READ tools. Anything else is refused before it reaches the network,
  including every cart, order, checkout, address-changing and payment tool.
- Writes are not implemented here yet: live writes arrive with their guard rails (T6.3). In
  dry-run the `WriteGate` never reaches a provider at all.
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
ADDRESS_PAGE_SIZE = 10  # the tool's documented maximum
MAX_ADDRESS_PAGES = 3
READ_TIMEOUT_S = 45.0  # one real call took about 16 s in Spike A


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


def _int(p: dict[str, Any], key: str, default: int = 0) -> int:
    v = p.get(key, default)
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise _BadParams(f"{key} must be a non-negative integer")
    return v
