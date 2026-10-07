"""About page (T5.5). Wording approved by the owner on 2026-10-07: change it only on request."""

import streamlit as st

st.set_page_config(page_title="About · MoodMeals", page_icon="ℹ️")
st.title("About MoodMeals")
st.markdown(
    """
**What it is.** A portfolio project: an agent that helps you decide between cooking at home
and ordering in, asks at most three questions, proposes one plan and stops for your approval.

**What it is not.** It is not medical or nutrition advice, and it does not judge your mood.
It is a planning aid.

**Not affiliated with Swiggy.** This project is not approved, sponsored or endorsed by
Swiggy. The public site uses made-up restaurants and groceries only.

**Privacy.** The model never sees your address, phone number or order history. Nothing is
stored on a server. If you paste an API key on the public site, it passes through the app's
server to the model provider you chose and is kept in memory for the session only.

**Cost.** You bring your own key, or use the scripted demo for free. Cost figures are
estimates.

**Limits.** Results come from simulated data here. A model can still choose badly; the app
checks every plan against what it actually found before showing it, and nothing happens
without your approval.
"""
)
