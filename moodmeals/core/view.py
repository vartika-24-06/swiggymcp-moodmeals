"""What the model is allowed to see (design.md 6, the PII firewall; R12.1, DD4).

`build_model_view` whitelists fields: everything not named here is dropped by default.
The model never receives address text, phone numbers, names of other people, order
history or cart contents. Free text the person typed is redacted before it goes in.
Tool results are summarised to whitelisted fields and sit under `untrusted_data`.
"""

from __future__ import annotations

from typing import Any

from moodmeals.core.actions import READ_TOOLS
from moodmeals.core.guard import RunBudget
from moodmeals.core.redaction import redact_text
from moodmeals.core.state import RunState
from moodmeals.models.types import MenuItem, Product, Restaurant
from moodmeals.tools.compact import view_menu_items, view_products, view_restaurants

MAX_ITEMS_IN_VIEW = 30  # design DQ2: a menu page is about 2.7k tokens uncapped

TOOL_SPECS = {
    "search_restaurants": {"query": "dish or cuisine, up to 60 chars", "offset": "optional"},
    "get_menu": {"restaurant_id": "from a search result", "page": "optional", "page_size": "1-8"},
    "search_dish": {
        "query": "dish name",
        "restaurant_id": "from a search result",
        "veg_only": "optional bool",
    },
    "search_products": {"query": "ingredient or product, up to 60 chars", "offset": "optional"},
}


def _keep_for_constraints(veg: str, state: RunState) -> bool:
    """With a veg constraint, hide items known to be non-veg or egg; keep unverified ones
    visible so the model can see (and the validator will reject) them."""
    return not (state.constraints.veg and veg in ("non_veg", "egg"))


def summarise_menu_items(items: list[MenuItem], state: RunState) -> dict[str, Any]:
    kept = [m for m in items if _keep_for_constraints(m.veg, state)]
    return {
        "total": len(items), "shown": min(len(kept), MAX_ITEMS_IN_VIEW),
        "items": view_menu_items(kept[:MAX_ITEMS_IN_VIEW]),
    }  # fmt: skip


def summarise_products(items: list[Product], state: RunState) -> dict[str, Any]:
    kept = [p for p in items if _keep_for_constraints(p.veg, state)]
    return {
        "total": len(items), "shown": min(len(kept), MAX_ITEMS_IN_VIEW),
        "products": view_products(kept[:MAX_ITEMS_IN_VIEW]),
    }  # fmt: skip


def summarise_restaurants(items: list[Restaurant]) -> dict[str, Any]:
    return {"total": len(items), "restaurants": view_restaurants(items)}


def build_model_view(state: RunState, budget: RunBudget | None = None) -> dict[str, Any]:
    b = budget or RunBudget()
    c = state.constraints
    view: dict[str, Any] = {
        "phase": state.phase,
        "mode": state.mode,
        "situation": redact_text(state.user_text, state._sensitive),
        "hard_constraints": {
            "vegetarian": c.veg, "budget_inr": c.budget, "exclusions": c.exclusions,
            "party_size": c.party_size,
        },
        "address": "confirmed" if state.address_handle else "not yet confirmed",
        "answers": [
            {
                "q": redact_text(a["q"], state._sensitive),
                "a": redact_text(a["a"], state._sensitive),
            }
            for a in state.answers
        ],
        "limits_left": {
            "questions": max(0, b.max_questions - state.questions_asked),
            "tool_calls": max(0, b.max_tool_calls - state.tool_calls),
            "iterations": max(0, b.max_iterations - state.iterations),
        },
        "available_tools": {name: TOOL_SPECS[name] for name in READ_TOOLS},
        "untrusted_data": {"tool_results": list(state.tool_log)},
        "rejected_plans": state.rejected_plans,
        "validation_errors": state.validation_errors,
        "notes": list(state.notes),
        "missing_signals": state.missing_signals,
    }  # fmt: skip
    return view
