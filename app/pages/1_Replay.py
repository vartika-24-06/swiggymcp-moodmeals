"""Replay page (T5.6): step through a recorded run. No model, no tools, no network."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ui_text  # noqa: E402

from moodmeals.core.events import Event  # noqa: E402
from moodmeals.core.replay import ReplayError, list_replays, load_replay  # noqa: E402

st.set_page_config(page_title="Replay · MoodMeals", page_icon="⏯️", layout="wide")
st.title("Replay ⏯️")
st.caption("Recorded mock runs. Playing one makes no model or tool calls.")

folder = Path(__file__).resolve().parents[2] / "data" / "replays"
files = list_replays(folder)
names = {p.stem: p for p in files}
choice = st.selectbox("Recorded run", list(names)) if names else None
upload = st.file_uploader("…or load an exported run", type="json")

data = None
try:
    if upload is not None:
        data = load_replay(upload.getvalue().decode("utf-8", "replace"))
    elif choice:
        data = load_replay(names[choice].read_text(encoding="utf-8"))
except ReplayError as e:
    st.error(str(e))

if data is None:
    st.info("No recorded runs yet. Export one from the Plan page.")
    st.stop()

try:
    events = [Event(**e) for e in data["events"]]
except Exception:
    st.error("This file's events are not in the expected format.")
    st.stop()

st.subheader(data.get("title", "Recorded run"))
st.caption(
    f"Model: {data.get('model', '?')} · prompt {data.get('prompt_version', '?')} · "
    f"{data.get('date', '')} · mock seed {data.get('mock_seed', '?')}"
)
shown = st.slider("Step", 0, len(events), len(events))
for row in ui_text.trace_rows(events[:shown]):
    st.write(f"**{row['step']}** · {row['who']} · {row['what']}")
    if row["why"]:
        st.caption(row["why"])
if shown == len(events) and data.get("outcome"):
    st.info(f"Outcome: {data['outcome'].get('kind')} {data['outcome'].get('message', '')}")
