"""Run limits and the question budget, enforced in code (design.md 9.2 and 10.1; R1.2, R11).

Values are proposed defaults, not final. Everything here is a pure function of the
`RunState` counters plus a clock, so each limit is testable without a model.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from moodmeals.core.state import RunState

StopReason = Literal["cancelled", "max_iterations", "max_tool_calls", "max_seconds"]


@dataclass(frozen=True)
class RunBudget:
    max_iterations: int = 12
    max_tool_calls: int = 20
    max_seconds: int = 90
    max_questions: int = 3
    max_validation_retries: int = 2
    # One real Swiggy call took about 16 s in Spike A (design 10.1), so real-mode limits are wider.
    tool_timeout_s: int = 45

    @classmethod
    def for_mode(cls, mode: str) -> RunBudget:
        """Mock runs are fast; real Swiggy runs need room for slow tool calls."""
        if mode == "mock":
            return cls()
        return cls(max_iterations=14, max_tool_calls=20, max_seconds=300)


class Guard:
    def __init__(self, budget: RunBudget | None = None, clock: Callable[[], float] = time.time):
        self.budget = budget or RunBudget()
        self._clock = clock

    def check(self, state: RunState) -> StopReason | None:
        """Called at the start of every step. Returns why the run must stop, or None."""
        b = self.budget
        if state.cancelled:
            return "cancelled"
        if state.iterations >= b.max_iterations:
            return "max_iterations"
        if state.tool_calls >= b.max_tool_calls:
            return "max_tool_calls"
        if self._clock() - state.started_at >= b.max_seconds:
            return "max_seconds"
        return None

    def allow_question(self, state: RunState) -> bool:
        return state.questions_asked < self.budget.max_questions

    def record_question(self, state: RunState) -> bool:
        """Count a question if the budget allows. False means refuse it (R1.2)."""
        if not self.allow_question(state):
            return False
        state.questions_asked += 1
        return True

    def allow_validation_retry(self, state: RunState) -> bool:
        return state.validation_retries < self.budget.max_validation_retries

    def record_validation_retry(self, state: RunState) -> bool:
        if not self.allow_validation_retry(state):
            return False
        state.validation_retries += 1
        return True
