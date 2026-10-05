"""Plan page (tasks T5.1-T5.4, design 11). The loop is a paused state machine; every click is
a callback that changes the run, then the script re-renders from `RunState`."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))  # lets `config` and `ui_text` import

import config  # noqa: E402
import ui_text  # noqa: E402

from moodmeals.core.guard import Guard, RunBudget  # noqa: E402
from moodmeals.core.loop import Agent  # noqa: E402
from moodmeals.core.replay import export_run  # noqa: E402
from moodmeals.core.validator import Constraints  # noqa: E402
from moodmeals.models.adapters import make_client  # noqa: E402
from moodmeals.models.demo import DemoLLM  # noqa: E402
from moodmeals.models.llm import LLMError  # noqa: E402
from moodmeals.models.pricing import estimate_run_cost  # noqa: E402
from moodmeals.models.schema import PROMPT_VERSION  # noqa: E402
from moodmeals.providers.mock import MockProvider  # noqa: E402

SEED = 1
st.set_page_config(page_title="MoodMeals", page_icon="🍽️", layout="wide")

try:
    MODE = config.resolve_mode()
except config.ModeRefused as e:
    st.error(str(e))
    st.stop()

S = st.session_state
S.setdefault("agent", None)
S.setdefault("state", None)
S.setdefault("llm", None)
S.setdefault("t0", 0.0)
S.setdefault("elapsed", 0.0)
S.setdefault("needs_run", False)
S.setdefault("draft", "")
S.setdefault("conn", None)


def reset() -> None:
    S.agent = S.state = S.llm = None
    S.needs_run = False


def start() -> None:
    label, model = S["provider_label"], S.get("model_name", "").strip()
    provider = config.PROVIDERS[label]
    try:
        if provider == "demo":
            llm = DemoLLM()
        else:
            llm = make_client(provider, model, S.get("api_key", "").strip())
    except LLMError as e:
        S["start_error"] = str(e)
        return
    S["start_error"] = ""
    budget = S.get("budget_in") or None
    cons = Constraints(veg=bool(S.get("veg_in")), budget=int(budget) if budget else None)
    if MODE == "dry_run":
        if S.get("conn") is None:
            S["start_error"] = "Connect to Swiggy first (sidebar)."
            return
        from moodmeals.providers.swiggy import SwiggyProvider

        provider_obj = SwiggyProvider(S.conn, "dry_run")
    else:
        provider_obj = MockProvider(seed=SEED, n_addresses=int(S.get("n_addr", 1)))
    S.llm = llm
    S.agent = Agent(llm, provider_obj, Guard(RunBudget.for_mode(MODE)))
    S.state = S.agent.start(S["draft"] or "Kya khana hai, batao", cons)
    S.t0, S.needs_run = time.time(), True


def answer(text: str) -> None:
    S.agent.provide_answer(S.state, text)
    S.needs_run = True


def pick_address(handle: str) -> None:
    S.agent.choose_address(S.state, handle)
    S.needs_run = True


def approve() -> None:
    S.agent.approve(S.state)
    S.needs_run = S.state.phase not in ("DONE", "STOPPED", "AWAITING_APPROVAL")


def another_idea() -> None:
    S.agent.reject(S.state)
    S.needs_run = True


def stop_run() -> None:
    S.agent.cancel(S.state)


# ---------------------------------------------------------------- sidebar

with st.sidebar:
    st.header("Setup")
    if MODE == "mock":
        st.caption("Mode: **mock** (simulated data, nothing is sent to Swiggy)")
    else:
        st.caption(
            "Mode: **dry-run**. Real read-only Swiggy data. Nothing is added to a cart or ordered."
        )
        if S.conn is None:
            if st.button("Connect to Swiggy"):
                from moodmeals.providers.mcp_connection import ConnectionFailed, McpConnection

                st.info("Sign in in the browser tab that opens: once for Food, once for Instamart.")
                conn = McpConnection()
                try:
                    with st.spinner("Waiting for sign-in…"):
                        conn.connect("food")
                        conn.connect("im")
                    S.conn = conn
                    st.rerun()
                except ConnectionFailed as e:
                    conn.close()
                    st.error(str(e))
        else:
            st.success("Connected to Swiggy (Food and Instamart)")
        st.checkbox("Hide address text (for screen recording)", value=True, key="hide_addr")
    st.selectbox("Model provider", list(config.PROVIDERS), key="provider_label")
    prov = config.PROVIDERS[S["provider_label"]]
    if prov == "demo":
        st.info("A simple rule-based stand-in. No key, no cost. Not a real model.")
    else:
        st.text_input("Model name", value=config.SUGGESTED_MODELS.get(prov, ""), key="model_name")
        st.text_input("API key", type="password", key="api_key")
        if config.is_public():
            st.warning(config.PASS_THROUGH_NOTICE)
        est = estimate_run_cost(prov, S.get("model_name", "") or "")
        st.caption(
            "Estimated cost per run: "
            + ("unknown for this model" if est is None else f"about ${est:.3f}")
            + " (an estimate; prices may be out of date)"
        )
    if MODE == "mock":
        st.number_input("Saved addresses in the mock world", 1, 5, 1, key="n_addr")
    st.caption(f"Prompt version: {PROMPT_VERSION}")

# ---------------------------------------------------------------- main

st.title("MoodMeals 🍽️")
st.caption("Kya khana hai, batao. " + config.DISCLAIMER)

state, agent = S.state, S.agent

if state is None:
    st.text_area(
        "What's the situation?",
        key="draft",
        placeholder="e.g. Bahut thaka hua hoon, kuch halka chahiye",
    )
    cols = st.columns(3)
    for c, quick in zip(
        cols,
        ["Thaka hua hoon, kuch halka", "Ghar pe kuch simple banana hai", "Kuch bhi, bas jaldi"],
        strict=True,
    ):
        c.button(quick, on_click=lambda q=quick: S.update(draft=q), width="stretch")
    c1, c2 = st.columns(2)
    c1.checkbox("Vegetarian only", key="veg_in")
    c2.number_input("Budget in ₹ (0 = none)", 0, 5000, 0, step=50, key="budget_in")
    st.button("Plan my meal", type="primary", on_click=start)
    if S.get("start_error"):
        st.error(S["start_error"])
    st.stop()

# Drive the loop until the person is needed.
if S.needs_run:
    S.needs_run = False
    with st.status("Thinking…", expanded=True) as status:
        for event in agent.run_until_pause(state):
            if event.type not in ("user_input",):
                st.write(f"{ui_text.ACTOR_ICON.get(event.actor, '')} {ui_text.event_line(event)}")
        status.update(label="Ready", state="complete", expanded=False)
    S.elapsed = time.time() - S.t0

st.write(f"**You:** {state.user_text}")
if state.address_label:
    st.caption(f"Delivering to your saved **{state.address_label}** address.")

# ---- waiting for the person
if state.waiting == "answer" and state.pending_question:
    q = state.pending_question
    st.subheader(q["question"])
    st.caption(f"Question {state.questions_asked} of {agent.guard.budget.max_questions}")
    opts = q.get("options") or []
    for col, opt in zip(st.columns(max(1, len(opts))), opts, strict=False):
        col.button(opt, key=f"opt_{opt}", on_click=answer, args=(opt,), width="stretch")
    with st.form("free_answer", clear_on_submit=True):
        txt = st.text_input("Or type your own answer")
        if st.form_submit_button("Send") and txt.strip():
            answer(txt.strip())
            st.rerun()

elif state.waiting == "address":
    st.subheader("Which address should I use?")
    st.caption("Counts as one of your questions.")
    hide = MODE != "mock" and S.get("hide_addr", True)
    for i, opt in enumerate(agent.address_options(state), 1):
        shown = (
            f"{opt['label']} (address hidden, #{i})" if hide else f"{opt['label']}: {opt['text']}"
        )
        st.button(
            shown, key=opt["handle"],
            on_click=pick_address, args=(opt["handle"],), width="stretch",
        )  # fmt: skip

# ---- plan and approval
if state.plan and state.phase == "AWAITING_APPROVAL":
    plan = state.plan
    with st.container(border=True):
        st.subheader("Cook at home" if plan.path == "cook" else "Order in")
        st.write(plan.reason)
        for it in plan.items:
            st.write(f"• {it.qty} × {it.name} — ₹{it.unit_price * it.qty}")
        st.write(f"**Item total: ₹{plan.item_total}**")
        if plan.eta_minutes:
            st.caption(f"About {plan.eta_minutes} min delivery")
        if plan.assumptions:
            st.caption("Assumptions: " + "; ".join(plan.assumptions))
        for w in state.warnings:
            st.warning(w["message"])
        for note in plan.mode_notes:
            st.caption(note)
        st.caption(config.DISCLAIMER)
    a = ui_text.approval_text(state)
    with st.container(border=True):
        st.markdown(f"**{a['step']}: {a['what']}**")
        if a["note"]:
            st.caption(a["note"])
        if a["warning"]:
            st.warning(a["warning"])
        b1, b2, b3 = st.columns(3)
        b1.button(a["button"], type="primary", on_click=approve, width="stretch")
        b2.button("Another idea", on_click=another_idea, width="stretch")
        b3.button("Stop", on_click=stop_run, width="stretch")

# ---- the end
if state.phase in ("DONE", "STOPPED") and state.outcome:
    out = state.outcome
    if out["kind"] == "stopped":
        st.error(out["message"])
    elif out["kind"] == "order_placed":
        st.success("Done (simulated). No real order was placed.")
    elif out["kind"] == "order_not_placed":
        st.info(out["message"])
    else:
        st.info(out.get("message", "Done."))
    st.button("Start over", on_click=reset)
    if out.get("would_do"):
        with st.expander("What would have happened"):
            st.json(out["would_do"])
    if state.mode == "mock":
        run = export_run(
            state, seed=SEED, model=getattr(S.llm, "model", "?"), prompt_version=PROMPT_VERSION
        )
        st.download_button(
            "Export this run (mock only)",
            json.dumps(run, indent=1, default=str),
            file_name=f"moodmeals_{state.run_id}.json",
            mime="application/json",
        )
elif state.phase not in ("AWAITING_APPROVAL",) and state.waiting is None and state.plan is None:
    st.button("Stop", on_click=stop_run, key="stop2")

# ---- trace
with st.expander("Trace: what the agent did", expanded=False):
    sm = ui_text.run_summary(state, S.llm, S.elapsed or (time.time() - S.t0))
    m = st.columns(5)
    m[0].metric("Tool calls", sm["tool_calls"])
    m[1].metric("Questions", sm["questions"])
    m[2].metric("Tokens in/out", f"{sm['tokens_in']}/{sm['tokens_out']}")
    cost = sm["cost_usd"]
    m[3].metric("Est. cost", "unknown" if cost is None else f"${cost:.4f}")
    m[4].metric("Seconds", sm["seconds"])
    st.dataframe(ui_text.trace_rows(state.events), width="stretch", hide_index=True)
