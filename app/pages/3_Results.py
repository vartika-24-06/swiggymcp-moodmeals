"""Results page (T7.6): the committed eval results, failures included. Reads files only."""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from evals import report  # noqa: E402
from evals.scenario import load_scenarios  # noqa: E402

st.set_page_config(page_title="Results · MoodMeals", page_icon="📊", layout="wide")
st.title("Results 📊")
st.caption(
    "How the agent did on 24 made-up scenarios. Counts are 'k of n', failures are listed, and "
    "samples are small (3 runs per scenario), so read them as evidence, not as precise rates."
)

files = report.find_results(ROOT / "evals" / "results")
if not files:
    st.info("No results file is committed yet.")
    st.stop()

path = st.selectbox("Results file", files, format_func=lambda p: p.name)
doc = report.load_results(path)
titles = {s.id: s.title for s in load_scenarios()}

prompts = doc.get("prompt_versions") or {doc.get("prompt_version"): doc.get("scenarios", [])}
st.write(
    f"**Model:** {doc.get('model')} · **Scenarios:** {len(doc.get('scenarios', []))} · "
    f"**Scored runs:** {doc.get('n_runs')} · **Spend:** ₹{doc.get('spent_inr')}"
)
if len(prompts) > 1:
    st.caption(
        "Prompt versions: "
        + "; ".join(f"{k} for {', '.join(v)}" for k, v in prompts.items())
        + " (re-run after a fix)."
    )

st.subheader("Runs passing every check")
counts = report.pass_counts(doc)
cols = st.columns(max(len(counts), 1))
for col, (strat, (k, n)) in zip(cols, counts.items(), strict=False):
    col.metric(report.STRATEGY_LABELS.get(strat, strat), f"{k} of {n}")
st.bar_chart(
    {report.STRATEGY_LABELS.get(s, s): [k] for s, (k, _) in counts.items()},
    y_label="runs passing every check",
)
st.caption(
    "Fixed workflow ran once per scenario (24 runs); the agent strategies ran about three "
    "times each. Compare the 'k of n' text, not the bar heights."
)

st.subheader("Check by check")
st.dataframe(report.check_table(doc), hide_index=True, width="stretch")

st.subheader("By scenario group")
groups = report.by_group(doc)
st.dataframe(
    [
        {"group": g.replace("_", " "), **{
            report.STRATEGY_LABELS.get(s, s): f"{k} of {n}" for s, (k, n) in v.items()
        }}
        for g, v in groups.items()
    ],
    hide_index=True,
    width="stretch",
)

st.subheader("Failures")
fails = report.failures(doc, titles)
st.write(f"{len(fails)} failed runs, all listed.")
if fails:
    st.dataframe(fails, hide_index=True, width="stretch")

st.subheader("Cost and speed per run")
cost_rows = [
    {"strategy": report.STRATEGY_LABELS.get(s, s), **v} for s, v in report.cost_summary(doc).items()
]
st.dataframe(
    cost_rows,
    hide_index=True,
    width="stretch",
)
excluded = doc.get("excluded_runs", [])
if excluded:
    st.caption(
        f"{len(excluded)} runs were left out because a model call stalled (a sleeping laptop), "
        "not because they failed."
    )
