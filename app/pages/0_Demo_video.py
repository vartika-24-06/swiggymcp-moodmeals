"""Demo video page: the real-Swiggy run, recorded on my own machine."""

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import links  # noqa: E402

st.set_page_config(page_title="Demo video · MoodMeals", page_icon="🎥", layout="wide")
st.title("Demo video 🎥", anchor=False)
st.caption(
    "The real-data run: read-only dry-run on my own Swiggy account, recorded on my machine. "
    "Swiggy sign-in only works on localhost, so this public site can't do it."
)
if hasattr(st, "iframe"):
    st.iframe(links.DEMO_VIDEO_EMBED, height=520)
else:  # older Streamlit
    import streamlit.components.v1 as components

    components.iframe(links.DEMO_VIDEO_EMBED, height=520)
st.markdown(f"[Open the video in a new tab]({links.DEMO_VIDEO_URL})")
