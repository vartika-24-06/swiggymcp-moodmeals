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
from moodmeals.core.ledger import Ledger
from moodmeals.core.validator import Constraints
from moodmeals.models.types import Plan

Phase = Literal[
    "CHECKIN", "GATHER", "PROPOSE", "VALIDATE", "AWAITING_APPROVAL", "EXECUTE", "DONE", "STOPPED"
]
Mode = Literal["mock", "dry_run", "live"]
Waiting = Literal["answer", "address"]
KEY_SIGNALS = ("budget", "party size", "diet")  # what R1.5 means by "required information"


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
    address_label: str | None = None  # "Home", "Work": a label, never address text
    plan: Plan | None = None
    events: list[Event] = Field(default_factory=list)

    # What the person said and decided (hard constraints are code-owned, design 9.1).
    user_text: str = ""
    constraints: Constraints = Field(default_factory=Constraints)
    answers: list[dict[str, str]] = Field(default_factory=list)  # {"q": ..., "a": ...}
    # Key details the person has stated or answered (budget, party size, diet) and the ones still
    # missing. Code-owned (R1.5): the validator requires listed assumptions when questions run out
    # with something missing, and the model sees `missing_signals`.
    stated_signals: list[str] = Field(default_factory=list)
    missing_signals: list[str] = Field(default_factory=lambda: list(KEY_SIGNALS))
    waiting: Waiting | None = None  # the run is paused for the person (not for approval)
    pending_question: dict[str, Any] | None = None

    # What the agent has seen and tried.
    ledger: Ledger = Field(default_factory=Ledger)
    tool_log: list[dict[str, Any]] = Field(default_factory=list)  # compact results for the model
    notes: list[str] = Field(default_factory=list)  # corrective messages for the next turn
    validation_errors: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    rejected_plans: list[dict[str, Any]] = Field(default_factory=list)
    protocol_errors: int = 0

    # Approval flow: the plan is approved first (cart), then the order separately (R10.3).
    pending_write: Literal["cart", "order"] | None = None
    outcome: dict[str, Any] | None = None
    # Live mode only: was the cart empty when last checked? A status, never the contents (DQ6).
    cart_check: Literal["empty", "not_empty", "unknown"] | None = None
    replace_confirmed: bool = False  # the person agreed to change a cart that has items

    # Real strings to scrub from every event (e.g. the chosen address). Not serialised.
    _sensitive: set[str] = PrivateAttr(default_factory=set)
    # handle -> real id, and handle -> address text for the UI picker. Memory only (R12.2).
    _address_ids: dict[str, str] = PrivateAttr(default_factory=dict)
    _address_display: list[dict[str, str]] = PrivateAttr(default_factory=list)

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
