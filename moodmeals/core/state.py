"""Serialisable run state (design.md 4 and 11).

The loop is a resumable state machine over this object, so it must round-trip to
JSON (Streamlit reruns the script on every click). Real address text is never a
field: it lives in a private attribute that is not serialised.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from moodmeals.core.events import Actor, Event, EventType, make_event
from moodmeals.models.types import Plan

Phase = Literal[
    "CHECKIN", "GATHER", "PROPOSE", "VALIDATE", "AWAITING_APPROVAL", "EXECUTE", "DONE", "STOPPED"
]
Mode = Literal["mock", "dry_run", "live"]


class RunState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    mode: Mode = "mock"
    phase: Phase = "CHECKIN"
    started_at: float = Field(default_factory=time.time)
    step: int = 0
    iterations: int = 0
    tool_calls: int = 0
    questions_asked: int = 0
    validation_retries: int = 0
    cancelled: bool = False
    stop_reason: str | None = None
    address_handle: str | None = None  # opaque, e.g. "address_1"; never the text
    plan: Plan | None = None
    events: list[Event] = Field(default_factory=list)

    # Real strings to scrub from every event (e.g. the chosen address). Not serialised.
    _sensitive: set[str] = PrivateAttr(default_factory=set)

    def register_sensitive(self, *values: str) -> None:
        self._sensitive.update(v for v in values if v)

    def add_event(
        self,
        type: EventType,
        actor: Actor,
        payload: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Event:
        event = make_event(
            self.run_id, self.step, type, actor, payload, known_sensitive=self._sensitive, **kwargs
        )
        self.events.append(event)
        self.step += 1
        return event

    def to_json(self) -> str:
        return self.model_dump_json()

    @classmethod
    def from_json(cls, text: str) -> RunState:
        return cls.model_validate_json(text)
