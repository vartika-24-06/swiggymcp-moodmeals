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
    state: RunState,
    *,
    seed: int,
    model: str,
    prompt_version: str,
    title: str = "",
    note: str = "",
    tag: str = "",
) -> dict[str, Any]:
    """`note` says what to notice; `tag` is "success" or "failure" (the replay gallery)."""
    if state.mode != "mock":
        raise ReplayError("Only mock-mode runs can be exported")
    return {
        "version": 1,
        "title": title or state.user_text[:60],
        **({"note": note} if note else {}),
        **({"tag": tag} if tag else {}),
        "run_id": state.run_id,
        "mock_seed": seed,
        "model": model,
        "prompt_version": prompt_version,
        "date": time.strftime("%Y-%m-%d"),
        "outcome": state.outcome,
        "events": [e.model_dump() for e in state.events],
    }


def replay_from_trace(
    trace: list[dict[str, Any]],
    *,
    title: str,
    note: str,
    seed: int,
    model: str,
    prompt_version: str,
    date: str,
    tag: str = "failure",
) -> dict[str, Any]:
    """A replay built from a failed run's trace in an eval results file (mock data only).

    The trace keeps step, type, actor, payload and rationale; the rest of an event is filled
    in. The outcome is read from the last stop event."""
    events = [
        {
            "run_id": "evalrun",
            "step": e["step"],
            "ts": 0.0,
            "type": e["type"],
            "actor": e["actor"],
            "payload": e.get("payload") or {},
            "rationale": e.get("rationale"),
            "tokens_in": 0,
            "tokens_out": 0,
            "latency_ms": 0,
        }
        for e in trace
    ]
    stop = next((e for e in reversed(trace) if e["type"] == "stop"), None)
    outcome = (
        {"kind": "stopped", "message": f"Stopped: {stop['payload'].get('reason', 'unknown')}"}
        if stop
        else None
    )
    return {
        "version": 1,
        "title": title,
        "note": note,
        "tag": tag,
        "run_id": "evalrun",
        "mock_seed": seed,
        "model": model,
        "prompt_version": prompt_version,
        "date": date,
        "outcome": outcome,
        "events": events,
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
