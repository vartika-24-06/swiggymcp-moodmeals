"""The model client contract (design.md 7.4)."""

from __future__ import annotations

from typing import Any, Protocol


class LLMError(Exception):
    """The model call failed (bad key, rate limit, network). The run ends plainly (N5)."""


class LLMClient(Protocol):
    def next_action(self, view: dict[str, Any]) -> Any:
        """Return one raw action: {"action": ..., "args": {...}, "rationale": "..."}.

        Adapters convert a vendor's native tool calling into this shape.
        """
        ...
