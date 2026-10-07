# ruff: noqa: E501
"""The replay gallery (tasks T8.1; requirement R15.1): recorded mock runs that play with no
key, no model and no network, including failures."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from moodmeals.core.events import Event
from moodmeals.core.replay import ReplayError, list_replays, load_replay, replay_from_trace

ROOT = Path(__file__).resolve().parents[1]
GALLERY = ROOT / "data" / "replays"
sys.path.insert(0, str(ROOT / "app"))

import ui_text  # noqa: E402


def gallery():
    return [(p, load_replay(p.read_text(encoding="utf-8"))) for p in list_replays(GALLERY)]


def test_the_gallery_has_at_least_six_runs_including_two_failures():
    runs = gallery()
    assert len(runs) >= 6
    tags = [d.get("tag") for _, d in runs]
    assert tags.count("failure") >= 2 and tags.count("success") >= 4


def test_every_run_is_a_valid_mock_replay_with_a_title_and_a_note():
    for path, d in gallery():
        assert d["title"] and d["note"] and d["tag"] in ("success", "failure"), path.name
        assert "mock_seed" in d and d["model"] and d["prompt_version"], path.name
        events = [Event(**e) for e in d["events"]]  # every event is in the expected format
        assert events and events[0].type == "user_input", path.name
        assert [e.step for e in events] == sorted(e.step for e in events), path.name


def test_the_gallery_holds_only_synthetic_data():
    for path, d in gallery():
        events = [{k: v for k, v in e.items() if k != "ts"} for e in d["events"]]  # ts is a time
        text = json.dumps({**d, "events": events})
        assert not re.search(r"\b\d{10}\b", text), path.name  # phone-like numbers
        assert "@" not in text and "http" not in text, path.name
        assert "Flat 101" not in text and "XXXXXXXX" not in text, path.name  # mock address text


def test_no_replay_contains_a_real_write():
    for path, d in gallery():
        writes = [e for e in d["events"] if e["type"] == "write_executed"]
        assert all(e["payload"].get("status") == "simulated" for e in writes), path.name


def test_the_failures_show_a_real_failure_not_a_staged_success():
    by_tag = {d["tag"]: [] for _, d in gallery()}
    for _, d in gallery():
        by_tag[d["tag"]].append(d)
    real = next(d for d in by_tag["failure"] if "real model" in d["title"])
    assert real["outcome"]["kind"] == "stopped" and real["model"] != "scripted-demo"
    baseline = next(d for d in by_tag["failure"] if "baseline" in d["title"])
    plan = next(e for e in baseline["events"] if e["type"] == "plan")
    assert plan["payload"]["path"] == "order_in"  # the person wanted to cook


def test_the_budget_replay_changes_the_dish_after_the_change():
    d = next(d for _, d in gallery() if "budget" in d["title"].lower())
    plans = [e["payload"] for e in d["events"] if e["type"] == "plan"]
    assert len(plans) == 2 and plans[0]["items"][0]["name"] != plans[1]["items"][0]["name"]
    assert plans[1]["item_total"] <= 120 < plans[0]["item_total"]


def test_replay_from_trace_builds_a_loadable_replay():
    trace = [
        {
            "step": 0,
            "type": "user_input",
            "actor": "user",
            "payload": {"text": "x"},
            "rationale": None,
        },
        {
            "step": 1,
            "type": "stop",
            "actor": "code",
            "payload": {"reason": "no_option"},
            "rationale": None,
        },
    ]
    d = replay_from_trace(
        trace, title="t", note="n", seed=1, model="m", prompt_version="p", date="2026-10-07"
    )
    assert [Event(**e).type for e in d["events"]] == ["user_input", "stop"]
    assert d["outcome"]["message"] == "Stopped: no_option" and d["tag"] == "failure"
    json.dumps(d)
    with pytest.raises(ReplayError):
        load_replay("not json")


def test_trace_rows_show_model_call_latency_when_recorded():
    e = Event(
        run_id="r",
        step=0,
        ts=0.0,
        type="tool_call",
        actor="model",
        payload={"tool": "x"},
        latency_ms=840,
    )
    assert ui_text.trace_rows([e])[0]["ms"] == 840
    quiet = Event(run_id="r", step=1, ts=0.0, type="plan", actor="model", payload={})
    assert ui_text.trace_rows([quiet])[0]["ms"] is None


def test_the_replay_page_plays_every_recorded_run_without_errors():
    for path, d in gallery():
        at = AppTest.from_file(str(ROOT / "app" / "pages" / "1_Replay.py"), default_timeout=30)
        at.run()
        assert not at.exception, at.exception
        label = next(o for o in at.selectbox[0].options if d["title"] in o)
        at.selectbox[0].select(label).run()
        assert not at.exception, (path.name, at.exception)
        assert at.subheader[0].value == d["title"]
        notes = [w.value for w in at.warning] + [i.value for i in at.info]
        assert d["note"] in notes, path.name
        assert not at.error or d["outcome"]["kind"] == "stopped"  # a stop is shown as an outcome
