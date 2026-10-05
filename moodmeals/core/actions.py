"""The four things the model may do each turn (design.md 4.2), parsed strictly.

The model returns {"action": <kind>, "args": {...}, "rationale": "<one line>"}. Anything
else is a protocol error: the loop gives one corrective message, then stops cleanly.
Tool parameters are structured and checked here: free user text is never passed through
to a tool (R11.3), and ids must have been seen in this run (R8.1).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any, Literal

from moodmeals.core.ledger import Ledger

READ_TOOLS = ("search_restaurants", "get_menu", "search_dish", "search_products")
QUESTION_FIELDS = ("diet", "budget", "time", "party", "preference", "other")
MAX_TEXT = 200


class ProtocolError(Exception):
    """The model's output was not a valid action."""


@dataclass(frozen=True)
class ToolCallAction:
    name: str
    params: dict[str, Any]
    rationale: str = ""


@dataclass(frozen=True)
class AskAction:
    question: str
    field: str = "other"
    options: list[str] = dc_field(default_factory=list)
    rationale: str = ""


@dataclass(frozen=True)
class ProposeAction:
    path: Literal["cook", "order_in"]
    reason: str
    items: list[dict[str, Any]]
    restaurant_id: str | None = None
    assumptions: list[str] = dc_field(default_factory=list)
    rationale: str = ""


@dataclass(frozen=True)
class StopAction:
    """Give up with a reason: neither ordering in nor a quick Instamart meal is possible."""

    reason: str
    rationale: str = ""


Action = ToolCallAction | AskAction | ProposeAction | StopAction


def _text(value: Any, name: str, required: bool = True) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise ProtocolError(f"{name} must be a non-empty string")
    return value.strip()[:MAX_TEXT]


def parse_action(raw: Any) -> Action:
    if not isinstance(raw, dict):
        raise ProtocolError("Reply with one JSON object: {action, args, rationale}")
    if isinstance(raw.get("error"), str):  # an adapter could not read the reply
        raise ProtocolError(raw["error"])
    kind, args = raw.get("action"), raw.get("args")
    if kind not in ("tool_call", "ask_user", "propose_plan", "stop_search"):
        raise ProtocolError("action must be one of tool_call, ask_user, propose_plan, stop_search")
    if not isinstance(args, dict):
        raise ProtocolError("args must be an object")
    rationale = _text(raw.get("rationale"), "rationale", required=False)

    if kind == "tool_call":
        name, params = args.get("name"), args.get("params", {})
        if name not in READ_TOOLS:
            raise ProtocolError(f"tool must be one of {', '.join(READ_TOOLS)}")
        if not isinstance(params, dict):
            raise ProtocolError("params must be an object")
        return ToolCallAction(name, params, rationale)

    if kind == "ask_user":
        options = args.get("options") or []
        if not isinstance(options, list) or len(options) > 4:
            raise ProtocolError("options must be a list of at most 4 short strings")
        fld = args.get("field", "other")
        if fld not in QUESTION_FIELDS:
            raise ProtocolError(f"field must be one of {', '.join(QUESTION_FIELDS)}")
        return AskAction(
            _text(args.get("question"), "question"),
            fld,
            [_text(o, "option")[:40] for o in options],
            rationale,
        )

    if kind == "stop_search":
        return StopAction(_text(args.get("reason"), "reason"), rationale)

    path = args.get("path")
    if path not in ("cook", "order_in"):
        raise ProtocolError("path must be cook or order_in")
    items = args.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 12:
        raise ProtocolError("items must be a list of 1 to 12 entries")
    clean: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict) or "id" not in it:
            raise ProtocolError("each item needs an id")
        qty = it.get("qty", 1)
        if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= 20:
            raise ProtocolError("qty must be a whole number from 1 to 20")
        entry: dict[str, Any] = {"id": str(it["id"]), "qty": qty}
        if it.get("variant_id") is not None:
            entry["variant_id"] = str(it["variant_id"])
        clean.append(entry)
    assumptions = args.get("assumptions") or []
    if not isinstance(assumptions, list):
        raise ProtocolError("assumptions must be a list of short strings")
    rid = args.get("restaurant_id")
    return ProposeAction(
        path,
        _text(args.get("reason"), "reason"),
        clean,
        str(rid) if rid is not None else None,
        [_text(a, "assumption") for a in assumptions][:6],
        rationale,
    )


def check_tool_params(name: str, params: dict[str, Any], ledger: Ledger) -> str | None:
    """Return a problem description, or None if the call is allowed."""
    allowed = {
        "search_restaurants": {"query", "offset"},
        "search_products": {"query", "offset"},
        "get_menu": {"restaurant_id", "page", "page_size"},
        "search_dish": {"query", "restaurant_id", "veg_only", "offset"},
    }[name]
    extra = set(params) - allowed
    if extra:
        return f"unexpected parameters: {', '.join(sorted(extra))}"
    if "query" in allowed:
        q = params.get("query")
        if not isinstance(q, str) or not 1 <= len(q.strip()) <= 60:
            return "query must be a short non-empty string (up to 60 characters)"
        if re.search(r"\d{6,}", q):
            return "query must not contain long numbers"
    if "restaurant_id" in allowed:
        rid = params.get("restaurant_id")
        if not isinstance(rid, str | int) or str(rid) not in ledger.restaurants:
            return "restaurant_id must come from a search_restaurants result in this run"
    for key, lo, hi in (("offset", 0, 200), ("page", 1, 10), ("page_size", 1, 8)):
        if key in params:
            v = params[key]
            if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
                return f"{key} must be a whole number from {lo} to {hi}"
    if "veg_only" in params and not isinstance(params["veg_only"], bool):
        return "veg_only must be true or false"
    return None
