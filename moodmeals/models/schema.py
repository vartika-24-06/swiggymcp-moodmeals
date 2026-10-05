"""The six functions the model may call, and the raw action shape (design 4.2, 7.4).

Native tool calling from every vendor is turned into
`{"action": "tool_call"|"ask_user"|"propose_plan", "args": {...}, "rationale": "..."}`
so the loop sees one contract whatever the vendor.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PROMPT_VERSION = "agent_v2"
_PROMPT_DIR = Path(__file__).resolve().parents[2] / "prompts"

RATIONALE = {"type": "string", "description": "One short line: why this action now"}


def _obj(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {**props, "rationale": RATIONALE},
        "required": [*required, "rationale"],
    }


_QUERY = {"type": "string", "description": "Short dish, cuisine or ingredient (up to 60 chars)"}
_RID = {"type": "string", "description": "A restaurant id from a search result"}

FUNCTIONS: list[dict[str, Any]] = [
    {
        "name": "search_restaurants",
        "description": "Search restaurants for the confirmed address. Read-only.",
        "parameters": _obj({"query": _QUERY, "offset": {"type": "integer"}}, ["query"]),
    },
    {
        "name": "get_menu",
        "description": "Get a restaurant's menu. Read-only.",
        "parameters": _obj(
            {"restaurant_id": _RID, "page": {"type": "integer"}, "page_size": {"type": "integer"}},
            ["restaurant_id"],
        ),
    },
    {
        "name": "search_dish",
        "description": "Search for a dish inside one restaurant. Read-only.",
        "parameters": _obj(
            {"query": _QUERY, "restaurant_id": _RID, "veg_only": {"type": "boolean"}},
            ["query", "restaurant_id"],
        ),
    },
    {
        "name": "search_products",
        "description": "Search grocery products to cook at home. Read-only.",
        "parameters": _obj({"query": _QUERY, "offset": {"type": "integer"}}, ["query"]),
    },
    {
        "name": "ask_user",
        "description": "Ask the person ONE short question when a key detail is missing.",
        "parameters": _obj(
            {
                "question": {"type": "string"},
                "field": {
                    "type": "string",
                    "enum": ["diet", "budget", "time", "party", "preference", "other"],
                },
                "options": {"type": "array", "items": {"type": "string"}},
            },
            ["question", "field"],
        ),
    },
    {
        "name": "propose_plan",
        "description": "Propose the plan once you have enough information.",
        "parameters": _obj(
            {
                "path": {"type": "string", "enum": ["cook", "order_in"]},
                "reason": {"type": "string", "description": "One sentence"},
                "restaurant_id": {"type": "string", "description": "Required for order_in"},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "variant_id": {"type": "string", "description": "Products only"},
                            "qty": {"type": "integer"},
                        },
                        "required": ["id", "qty"],
                    },
                },
                "assumptions": {"type": "array", "items": {"type": "string"}},
            },
            ["path", "reason", "items"],
        ),
    },
]
FUNCTION_NAMES = {f["name"] for f in FUNCTIONS}
_READ = {"search_restaurants", "get_menu", "search_dish", "search_products"}


def load_system_prompt(version: str = PROMPT_VERSION) -> str:
    return (_PROMPT_DIR / f"{version}.md").read_text(encoding="utf-8")


def to_raw_action(name: str | None, args: Any, n_calls: int = 1, text: str = "") -> dict[str, Any]:
    """Convert one native function call into the raw action shape.

    Anything unusable becomes a raw value `parse_action` rejects, so the loop's single
    corrective message and clean stop apply (design 4.2). The reply text is never trusted.
    """
    if n_calls > 1:
        return {"error": "Call exactly one function per turn"}
    if name is None:
        return {"error": "Reply by calling one function, not with text"}
    if name not in FUNCTION_NAMES or not isinstance(args, dict):
        return {"error": "Unknown function or bad arguments"}
    args = dict(args)
    rationale = args.pop("rationale", "")
    if name in _READ:
        return {
            "action": "tool_call",
            "args": {"name": name, "params": args},
            "rationale": rationale,
        }
    return {"action": name, "args": args, "rationale": rationale}
