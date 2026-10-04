"""Event schema for traces, replays and the eval scorer (design.md 12.1).

Events are redacted at creation, so nothing sensitive ever enters the log.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from moodmeals.core.redaction import redact_payload, redact_text

EventType = Literal[
    "user_input",
    "question",
    "tool_call",
    "tool_result_summary",
    "validation",
    "plan",
    "approval",
    "write_blocked",
    "write_executed",
    "stop",
    "error",
]
Actor = Literal["user", "model", "code"]


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    step: int = Field(ge=0)
    ts: float
    type: EventType
    actor: Actor
    payload: dict[str, Any] = Field(default_factory=dict)
    rationale: str | None = None  # one line shown in the trace; no hidden reasoning
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


def make_event(
    run_id: str,
    step: int,
    type: EventType,
    actor: Actor,
    payload: dict[str, Any] | None = None,
    *,
    rationale: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    latency_ms: int = 0,
    known_sensitive: Iterable[str] = (),
    ts: float | None = None,
) -> Event:
    """Create an event with its payload and rationale redacted."""
    known = list(known_sensitive)
    return Event(
        run_id=run_id,
        step=step,
        ts=time.time() if ts is None else ts,
        type=type,
        actor=actor,
        payload=redact_payload(payload or {}, known),
        rationale=redact_text(rationale, known) if rationale else None,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_ms=latency_ms,
    )
