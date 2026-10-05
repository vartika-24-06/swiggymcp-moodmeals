"""A scripted stand-in for the model, so loop tests cost nothing (tasks T3.4)."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

Step = dict[str, Any] | Callable[[dict[str, Any]], Any]


class FakeLLM:
    """Returns the scripted steps in order. A step is a raw action or a function of the view."""

    def __init__(self, script: list[Step]):
        self.script = list(script)
        self.views: list[dict[str, Any]] = []  # every view the "model" was shown

    def next_action(self, view: dict[str, Any]) -> Any:
        self.views.append(copy.deepcopy(view))
        if not self.script:
            raise AssertionError("FakeLLM script exhausted: the loop asked for another action")
        step = self.script.pop(0)
        return step(view) if callable(step) else step
