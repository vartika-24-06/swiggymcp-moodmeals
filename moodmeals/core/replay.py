"""Export and load recorded runs (design 12.2; R15.1). UI-independent.

Only mock-mode runs may be exported. Events were redacted when created, so a replay
file holds no addresses, phones or order history.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from moodmeals.core.state import RunState


class ReplayError(ValueError):
    pass


def export_run(
    state: RunState, *, seed: int, model: str, prompt_version: str, title: str = ""
) -> dict[str, Any]:
    if state.mode != "mock":
        raise ReplayError("Only mock-mode runs can be exported")
    return {
        "version": 1,
        "title": title or state.user_text[:60],
        "run_id": state.run_id,
        "mock_seed": seed,
        "model": model,
        "prompt_version": prompt_version,
        "date": time.strftime("%Y-%m-%d"),
        "outcome": state.outcome,
        "events": [e.model_dump() for e in state.events],
    }


def load_replay(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ReplayError("Not a valid replay file") from None
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        raise ReplayError("Not a valid replay file")
    return data


def list_replays(directory: Path) -> list[Path]:
    return sorted(directory.glob("*.json")) if directory.is_dir() else []
