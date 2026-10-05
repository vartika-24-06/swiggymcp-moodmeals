"""Provider contract (design.md 7.1).

Providers return payloads in the shapes Swiggy's tools return (observed 2026-10-04),
so the same parsers in `moodmeals.tools.normalise` serve the mock and the real thing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Protocol

Mode = Literal["mock", "dry_run", "live"]
ErrorKind = Literal["timeout", "error", "bad_params", "unknown_tool", "not_found"]


@dataclass(frozen=True)
class ToolError:
    kind: ErrorKind
    message: str


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: dict[str, Any] | None = None
    error: ToolError | None = None
    latency_ms: int = 0


class ActionProvider(Protocol):
    mode: Mode

    def call(self, tool: str, params: dict[str, Any]) -> ToolResult: ...
