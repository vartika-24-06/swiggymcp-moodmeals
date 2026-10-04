"""WriteGate: the only path to a write (design.md 7.2; R10).

Rules, in order:
1. Payment, address-changing and other forbidden tools are always blocked (R10.7).
2. A tool that is not a known write tool is blocked (fail closed).
3. Dry-run never calls the provider: it returns a "would do" preview (R10.2, R10.8).
4. Otherwise an approval must match this exact tool and params hash, be unused and
   unexpired. It is marked used BEFORE the call, so a failure is never retried
   automatically (design 10.2).
Mock mode follows the same approval rules, then calls the executor, which is the
in-memory MockProvider. Every call, allowed or blocked, is recorded (R10.4).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Mode = Literal["mock", "dry_run", "live"]
Tier = Literal["amber", "red"]

# Normalised names (design 5.1) and the raw Swiggy names, so neither route gets through.
WRITE_TIERS: dict[str, Tier] = {
    "update_food_cart": "amber",
    "update_cart": "amber",
    "flush_food_cart": "amber",
    "clear_cart": "amber",
    "place_food_order": "red",
    "checkout": "red",
}
FORBIDDEN_TOOLS = frozenset(
    {
        "get_payment_options",
        "check_payment_status",
        "confirm_order",
        "create_address",
        "delete_address",
        "apply_food_coupon",
    }
)


def params_hash(tool: str, params: dict[str, Any]) -> str:
    canonical = json.dumps({"tool": tool, "params": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@dataclass(frozen=True)
class WriteAction:
    tool: str
    params: dict[str, Any]

    @property
    def hash(self) -> str:
        return params_hash(self.tool, self.params)


@dataclass(frozen=True)
class Approval:
    id: str
    action_hash: str
    issued_at: float
    ttl_s: float


@dataclass(frozen=True)
class WriteOutcome:
    status: Literal["executed", "simulated", "dry_run_preview", "blocked", "failed"]
    reason: str
    tool: str
    action_hash: str
    result: Any = None


@dataclass
class WriteGate:
    mode: Mode
    executor: Callable[[str, dict[str, Any]], Any]
    ttl_s: float = 300.0
    clock: Callable[[], float] = time.time
    on_event: Callable[[str, dict[str, Any]], None] | None = None
    log: list[dict[str, Any]] = field(default_factory=list)
    _used: set[str] = field(default_factory=set)

    def issue_approval(self, action: WriteAction) -> Approval:
        """Called by the UI when the person approves this one action."""
        approval = Approval(uuid.uuid4().hex, action.hash, self.clock(), self.ttl_s)
        self._record(
            "approval", {"tool": action.tool, "action_hash": action.hash, "id": approval.id}
        )
        return approval

    def execute(self, action: WriteAction, approval: Approval | None) -> WriteOutcome:
        h = action.hash

        def blocked(reason: str) -> WriteOutcome:
            self._record("write_blocked", {"tool": action.tool, "action_hash": h, "reason": reason})
            return WriteOutcome("blocked", reason, action.tool, h)

        if action.tool in FORBIDDEN_TOOLS:
            return blocked("forbidden_tool")
        if action.tool not in WRITE_TIERS:
            return blocked("unknown_tool")
        if self.mode == "dry_run":
            self._record(
                "write_blocked", {"tool": action.tool, "action_hash": h, "reason": "dry_run"}
            )
            return WriteOutcome(
                "dry_run_preview",
                "dry_run",
                action.tool,
                h,
                result={"would_do": action.tool, "params": action.params},
            )
        if approval is None:
            return blocked("no_approval")
        if approval.action_hash != h:
            return blocked("params_mismatch")
        if approval.id in self._used:
            return blocked("already_used")
        if self.clock() - approval.issued_at > approval.ttl_s:
            return blocked("expired")

        self._used.add(approval.id)  # consumed before the call: never retried
        try:
            result = self.executor(action.tool, action.params)
        except Exception as e:  # noqa: BLE001 - any failure must be reported, not retried
            self._record("write_executed", {"tool": action.tool, "action_hash": h, "ok": False})
            return WriteOutcome("failed", type(e).__name__, action.tool, h)
        status = "executed" if self.mode == "live" else "simulated"
        self._record(
            "write_executed", {"tool": action.tool, "action_hash": h, "ok": True, "mode": self.mode}
        )
        return WriteOutcome(status, "ok", action.tool, h, result=result)

    def _record(self, kind: str, payload: dict[str, Any]) -> None:
        entry = {"type": kind, **payload}
        self.log.append(entry)
        if self.on_event:
            self.on_event(kind, payload)
