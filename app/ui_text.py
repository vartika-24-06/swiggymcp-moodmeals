"""Plain-text helpers for the pages: what to show, kept out of Streamlit so it can be tested."""

from __future__ import annotations

from typing import Any

from moodmeals.core.events import Event
from moodmeals.core.state import RunState

ACTOR_ICON = {"user": "🧑", "model": "🤖", "code": "⚙️"}


def event_line(e: Event) -> str:
    p = e.payload
    if e.type == "tool_call":
        return f"Called {p.get('tool')} {p.get('params')}"
    if e.type == "tool_result_summary":
        if "error" in p:
            return f"{p.get('tool')} failed: {p['error']}"
        if "count" in p:
            return f"{p.get('tool')} returned {p['count']} result(s)"
        return str(p.get("note", p))
    if e.type == "question":
        return str(p.get("question") or p.get("kind") or "Question")
    if e.type == "validation":
        return (
            "Plan verified" if p.get("ok") else f"Plan rejected: {', '.join(p.get('errors', []))}"
        )
    if e.type == "plan":
        return f"Proposed {p.get('path')}: {p.get('reason')}"
    if e.type == "approval":
        return f"{p.get('decision')} {p.get('tool') or p.get('step') or ''}".strip()
    if e.type in ("write_executed", "write_blocked"):
        return f"{e.type.replace('_', ' ')}: {p.get('tool')} ({p.get('status')})"
    if e.type == "stop":
        return f"Stopped: {p.get('reason')}"
    if e.type == "error":
        return f"Error: {p.get('detail') or p.get('kind')}"
    return str(p.get("text") or p.get("answer") or p.get("address_choice") or p)[:200]


def trace_rows(events: list[Event]) -> list[dict[str, Any]]:
    return [
        {
            "#": e.step,
            "who": f"{ACTOR_ICON.get(e.actor, '')} {e.actor}",
            "step": e.type,
            "what": event_line(e),
            "why": e.rationale or "",
        }
        for e in events
    ]


def approval_text(state: RunState) -> dict[str, Any]:
    """Exactly what the pending write will do (R10.3). One write per approval."""
    plan = state.plan
    assert plan is not None and state.pending_write
    live = state.mode == "live"
    if state.pending_write == "cart":
        what = (
            "Add these items to your Swiggy Food cart"
            if plan.path == "order_in"
            else "Put these items in your Instamart cart (this replaces what is already there)"
        )
        button, step = "Approve: update cart", "Step 1 of 2"
    else:
        what = "Place the order for the items in your cart"
        button, step = "Approve: place order", "Step 2 of 2"
    if live:
        sim = ""
    elif state.mode == "dry_run":
        sim = "Dry-run: approving only shows what would happen. Nothing is sent to Swiggy."
    else:
        sim = "Simulated: nothing is sent to Swiggy in this mode."
    warn = "Orders may not be reversible through these tools." if live else ""
    return {"step": step, "what": what, "button": button, "note": sim, "warning": warn}


def run_summary(state: RunState, llm: Any, elapsed_s: float) -> dict[str, Any]:
    usage = llm.usage() if hasattr(llm, "usage") else None
    cost = llm.cost_estimate() if hasattr(llm, "cost_estimate") else None
    return {
        "tool_calls": state.tool_calls,
        "questions": state.questions_asked,
        "model_calls": getattr(usage, "calls", 0),
        "tokens_in": getattr(usage, "tokens_in", 0),
        "tokens_out": getattr(usage, "tokens_out", 0),
        "cost_usd": cost,
        "seconds": round(elapsed_s, 1),
    }
