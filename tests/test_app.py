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


def test_dry_run_mode_with_fake_swiggy_connection(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_swiggy_provider import FakeConnection

    monkeypatch.setenv("MOODMEALS_MODE", "dry_run")
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["conn"] = FakeConnection()
    at.run()
    assert not at.exception, at.exception
    at.checkbox(key="veg_in").set_value(True)
    click(at, "Plan my meal")
    assert any("Step 1 of 2" in m.value for m in at.markdown)
    assert any("Dry-run: approving only shows" in c.value for c in at.caption)
    click(at, "Approve: update cart")
    assert any("Dry-run" in i.value for i in at.info)  # a preview, nothing written
    assert not any(b.label == "Approve: place order" for b in at.button)


def test_dry_run_requires_connecting_first(monkeypatch):
    monkeypatch.setenv("MOODMEALS_MODE", "dry_run")
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    click(at, "Plan my meal")
    assert any("Connect to Swiggy" in e.value for e in at.error)


def test_live_mode_is_refused_without_the_opt_in(monkeypatch):
    monkeypatch.setenv("MOODMEALS_MODE", "live")
    monkeypatch.delenv("MOODMEALS_ALLOW_LIVE", raising=False)
    at = AppTest.from_file(APP, default_timeout=30)
    at.run()
    assert any("Live mode" in e.value for e in at.error)


def test_live_mode_needs_the_flag_and_never_runs_on_the_public_site():
    assert config.resolve_mode({"MOODMEALS_MODE": "live", "MOODMEALS_ALLOW_LIVE": "1"}) == "live"
    for env in (
        {"MOODMEALS_MODE": "live"},
        {"MOODMEALS_MODE": "live", "MOODMEALS_ALLOW_LIVE": "yes"},
        {"MOODMEALS_MODE": "live", "MOODMEALS_ALLOW_LIVE": "1", "MOODMEALS_DEPLOY": "public"},
    ):
        with pytest.raises(config.ModeRefused):
            config.resolve_mode(env)


def _live_app(monkeypatch, conn):
    monkeypatch.setenv("MOODMEALS_MODE", "live")
    monkeypatch.setenv("MOODMEALS_ALLOW_LIVE", "1")
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["conn"] = conn
    at.run()
    assert not at.exception, at.exception
    return at


def test_live_mode_needs_the_session_acknowledgement(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_live_guards import LiveFake

    conn = LiveFake()
    at = _live_app(monkeypatch, conn)
    click(at, "Plan my meal")
    assert any("live-mode box" in e.value for e in at.error)
    assert conn.calls == []


def test_live_app_existing_cart_needs_confirmation_then_ends_without_an_order(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from test_live_guards import CART_ITEM, LiveFake

    conn = LiveFake(im_items=[CART_ITEM], food_items=[CART_ITEM])
    at = _live_app(monkeypatch, conn)
    at.checkbox(key="live_ack").set_value(True).run()
    at.checkbox(key="veg_in").set_value(True)
    click(at, "Plan my meal")
    assert any("Cart update (no order is placed)" in m.value for m in at.markdown)
    assert any("already has items" in w.value for w in at.warning)
    approve = next(b for b in at.button if b.label == "Approve: update cart")
    assert approve.disabled  # cannot approve over an existing cart until confirmed
    click(at, ui_text.approval_text(at.session_state["state"])["replace_button"])
    click(at, "Approve: update cart")
    assert [c[1] for c in conn.writes] and len(conn.writes) == 1
    assert at.session_state["state"].outcome["kind"] == "cart_updated"
    assert not any(b.label == "Approve: place order" for b in at.button)
    page = texts(at) + " ".join(i.value for i in at.info) + " ".join(i.value for i in at.success)
    assert "Zzyzx" not in page and "Synthetic Lane" not in page


def test_public_deployment_pages_all_render_and_requirements_are_present(monkeypatch):
    from pathlib import Path

    from streamlit.testing.v1 import AppTest

    root = Path(__file__).resolve().parents[1]
    assert (root / "requirements.txt").read_text().strip() == "-e ."
    monkeypatch.setenv("MOODMEALS_DEPLOY", "public")
    monkeypatch.delenv("MOODMEALS_MODE", raising=False)
    for page in ("app/Home.py", "app/pages/0_Demo_video.py", "app/pages/1_Replay.py",
                 "app/pages/3_Results.py"):
        at = AppTest.from_file(str(root / page), default_timeout=30).run()
        assert not at.exception, page
    home = AppTest.from_file(str(root / "app/Home.py"), default_timeout=30).run()
    assert not any("Connect to Swiggy" in b.label for b in home.button)
