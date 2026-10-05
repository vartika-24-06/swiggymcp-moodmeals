"""UI tests with Streamlit's AppTest, using the scripted demo model (no key, no network)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = str(Path(__file__).resolve().parents[1] / "app" / "Home.py")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import config  # noqa: E402
import ui_text  # noqa: E402


def fresh() -> AppTest:
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert not at.exception, at.exception
    return at


def click(at: AppTest, label: str) -> None:
    next(b for b in at.button if b.label == label).click().run()
    assert not at.exception, at.exception


def texts(at: AppTest) -> str:
    parts = [m.value for m in at.markdown] + [c.value for c in at.caption]
    return "\n".join(parts + [s.value for s in at.subheader])


def test_start_page_shows_disclaimer_and_no_key_needed():
    at = fresh()
    assert any("not advice" in c.value for c in at.caption)
    assert any(b.label == "Plan my meal" for b in at.button)


def test_full_run_ask_plan_approve_twice():
    at = fresh()
    at.text_area(key="draft").set_value("thaka hua hoon")
    click(at, "Plan my meal")
    assert any(s.value.startswith("Veg, non-veg") for s in at.subheader)  # a question
    click(at, "Veg")
    assert any("Order in" in s.value or "Cook at home" in s.value for s in at.subheader)
    assert any("Step 1 of 2" in m.value for m in at.markdown)
    click(at, "Approve: update cart")
    assert any("Step 2 of 2" in m.value for m in at.markdown)  # a separate approval
    click(at, "Approve: place order")
    assert any("simulated" in s.value.lower() for s in at.success)
    assert any(b.label == "Start over" for b in at.button)


def test_another_idea_gives_a_different_plan():
    at = fresh()
    at.checkbox(key="veg_in").set_value(True)
    click(at, "Plan my meal")
    first = [m.value for m in at.markdown if "•" in m.value]
    click(at, "Another idea")
    second = [m.value for m in at.markdown if "•" in m.value]
    assert first and second and first != second


def test_stop_ends_the_run():
    at = fresh()
    at.checkbox(key="veg_in").set_value(True)
    click(at, "Plan my meal")
    click(at, "Stop")
    assert any("Stopped" in e.value for e in at.error)


def test_address_picker_for_several_addresses():
    at = fresh()
    at.number_input(key="n_addr").set_value(3)
    at.checkbox(key="veg_in").set_value(True)
    click(at, "Plan my meal")
    assert any("Which address" in s.value for s in at.subheader)
    next(b for b in at.button if b.label.startswith("Home") or ":" in b.label).click().run()
    assert any("Order in" in s.value or "Cook at home" in s.value for s in at.subheader)


def test_missing_key_for_real_provider_shows_an_error_not_a_crash():
    at = fresh()
    at.selectbox(key="provider_label").set_value("OpenAI").run()
    click(at, "Plan my meal")
    assert any("key" in e.value.lower() for e in at.error)


def test_public_deployment_refuses_non_mock(monkeypatch):
    monkeypatch.setenv("MOODMEALS_DEPLOY", "public")
    monkeypatch.setenv("MOODMEALS_MODE", "dry_run")
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert any("mock mode only" in e.value for e in at.error)


def test_pass_through_notice_only_on_public(monkeypatch):
    monkeypatch.setenv("MOODMEALS_DEPLOY", "public")
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    at.selectbox(key="provider_label").set_value("Groq").run()
    assert any("passes through" in w.value for w in at.sidebar.warning)


def test_no_pii_in_rendered_page_or_trace():
    at = fresh()
    at.checkbox(key="veg_in").set_value(True)
    click(at, "Plan my meal")
    page = texts(at) + json.dumps(ui_text.trace_rows(at.session_state["state"].events))
    assert "Flat 101" not in page and "XXXXXXXX" not in page


def test_mode_resolution():
    assert config.resolve_mode({}) == "mock"
    with pytest.raises(config.ModeRefused):
        config.resolve_mode({"MOODMEALS_DEPLOY": "public", "MOODMEALS_MODE": "live"})
    with pytest.raises(config.ModeRefused):
        config.resolve_mode({"MOODMEALS_MODE": "bogus"})
