# ruff: noqa: E501
"""Merging parts of an eval run (scripts/merge_results.py). Synthetic results only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import merge_results as m  # noqa: E402


def row(sid, strat, run, ok=True, invalid=False):
    if invalid:
        return {
            "scenario_id": sid,
            "group": "g",
            "strategy": strat,
            "run": run,
            "invalid": True,
            "invalid_reason": "network",
        }
    return {
        "scenario_id": sid, "group": "g", "strategy": strat, "run": run, "passed": ok,
        "failed_checks": [] if ok else ["outcome"], "metrics": {"tool_calls": 2},
        "checks": {"outcome": ok, "path": None},
    }  # fmt: skip


def scenario_rows(sid, ok=True):
    return (
        [row(sid, "agent", i, ok) for i in (1, 2, 3)]
        + [row(sid, "agent_no_validator", i, ok) for i in (1, 2, 3)]
        + [row(sid, "fixed_workflow", 1, ok)]
    )


def doc(rows, commit="abc", spent=10.0, model="m", prompt="agent_v5", **kw):
    return {
        "version": 2,
        "date": "2026-10-07",
        "model": model,
        "provider": "p",
        "prompt_version": prompt,
        "git": {"commit": commit},
        "spent_inr": spent,
        "invalid_runs": 0,
        "results": rows,
        "strategies": ["agent"],
        "runs_per_scenario": 3,
        "cap_inr": 1500,
        **kw,
    }


def test_expand_ranges_and_lists():
    assert m.expand("S-01:S-03,S-09") == ["S-01", "S-02", "S-03", "S-09"]
    assert m.expand("s-18") == ["S-18"]
    for bad in ("S-5", "S-03:S-01", "X"):
        with pytest.raises(m.MergeError):
            m.expand(bad)


def test_merge_takes_each_scenario_from_its_part_and_recomputes_the_summary():
    a = doc(
        scenario_rows("S-01") + scenario_rows("S-02", ok=False) + [row("S-03", "agent", 1)], spent=5
    )
    b = doc(scenario_rows("S-03"), commit="def", spent=7)
    out = m.merge([(a, ["S-01", "S-02"], "a.json"), (b, ["S-03"], "b.json")])
    assert out["n_runs"] == 21 and out["scenarios"] == ["S-01", "S-02", "S-03"]
    assert out["spent_inr"] == 12.0 and [s["git_commit"] for s in out["merged_from"]] == [
        "abc",
        "def",
    ]
    assert (
        out["summary"]["agent"]["all_checks"]["text"] == "6 of 9"
    )  # S-02 failed in all 3 agent runs
    assert out["summary"]["fixed_workflow"]["all_checks"]["text"] == "2 of 3"
    assert [r["scenario_id"] for r in out["results"]][:2] == ["S-01", "S-01"]  # ordered
    json.dumps(out)


def test_invalid_runs_are_dropped_and_a_shortfall_is_reported_not_hidden():
    rows = scenario_rows("S-01")
    rows[4] = row("S-01", "agent_no_validator", 2, invalid=True)
    with pytest.raises(m.MergeError, match=r"S-01 agent_no_validator: 2 valid runs, expected 3"):
        m.merge([(doc(rows), ["S-01"], "a.json")])


def test_a_scenario_cannot_come_from_two_parts_and_parts_must_agree_on_model():
    a, b = doc(scenario_rows("S-01")), doc(scenario_rows("S-01"))
    with pytest.raises(m.MergeError, match="two parts"):
        m.merge([(a, ["S-01"], "a"), (b, ["S-01"], "b")])
    c = doc(scenario_rows("S-02"), model="other")
    with pytest.raises(m.MergeError, match="differ in model"):
        m.merge([(doc(scenario_rows("S-01")), ["S-01"], "a"), (c, ["S-02"], "c")])


def test_a_rerun_on_a_newer_prompt_is_merged_and_labelled_mixed():
    d = doc(scenario_rows("S-02"), prompt="agent_v6")
    out = m.merge([(doc(scenario_rows("S-01")), ["S-01"], "a"), (d, ["S-02"], "d")])
    assert out["prompt_version"] == "mixed"
    assert out["prompt_versions"] == {"agent_v5": ["S-01"], "agent_v6": ["S-02"]}


def test_command_line_writes_the_merged_file_and_reports_failures(tmp_path, capsys):
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(doc(scenario_rows("S-01") + scenario_rows("S-02"))), encoding="utf-8")
    pb.write_text(json.dumps(doc(scenario_rows("S-03"), commit="def")), encoding="utf-8")
    out = tmp_path / "merged.json"
    assert m.main(["x", str(out), f"{pa}=S-01:S-02", f"{pb}=S-03"]) == 0
    assert (
        json.loads(out.read_text(encoding="utf-8"))["n_runs"] == 21
        and "Wrote" in capsys.readouterr().out
    )
    assert (
        m.main(["x", str(tmp_path / "bad.json"), f"{pb}=S-03:S-04"]) == 1
    )  # S-04 is not in the part
    assert not (tmp_path / "bad.json").exists() and "Not merged" in capsys.readouterr().out
    assert m.main(["x", str(out), str(pa)]) == 2


def stalled_row(sid, strat, run, ms):
    r = row(sid, strat, run)
    r["metrics"] = {"tool_calls": 0, "latency_ms": ms, "slowest_call_ms": ms}
    return r


def test_a_run_with_a_stalled_model_call_is_excluded_and_reported_not_counted_as_a_failure():
    rows = scenario_rows("S-01")
    rows[2] = stalled_row("S-01", "agent", 3, 954_000)  # the laptop lid was closed mid-call
    out = m.merge([(doc(rows), ["S-01"], "a.json")], plan_must_include={})
    assert out["n_runs"] == 6 and out["summary"]["agent"]["all_checks"]["text"] == "2 of 2"
    [x] = out["excluded_runs"]
    assert (x["scenario_id"], x["strategy"], x["run"]) == ("S-01", "agent", 3) and "stalled" in x[
        "reason"
    ]
    short = scenario_rows("S-01")[:-1]  # a genuinely missing run is still an error
    with pytest.raises(m.MergeError, match="fixed_workflow: 0 valid runs"):
        m.merge([(doc(short), ["S-01"], "a.json")], plan_must_include={})


def failed_fallback_row(reason, names, sid="S-05", strat="agent", run=1):
    r = row(sid, strat, run, ok=False)
    r["checks"] = {"outcome": True, "fallback_plan": False}
    r["failed_checks"] = ["fallback_plan"]
    r["trace"] = [{"step": 3, "type": "plan", "actor": "model", "rationale": None,
                   "payload": {"reason": reason, "items": [{"name": n} for n in names]}}]  # fmt: skip
    return r


def test_fallback_plan_is_rescored_from_the_trace_under_the_current_word_lists():
    ok = failed_fallback_row(
        "Restaurant search timed out so ordering in isn’t possible; here is a khichdi mix.",
        ["Test Khichdi Mix"],
    )
    bad_item = failed_fallback_row(
        "Restaurants are closed.", ["Basmati Rice"], run=2
    )  # a staple, not a quick meal
    no_reason = failed_fallback_row("Here is something nice.", ["Instant Poha Cup"], run=3)
    rows = [ok, bad_item, no_reason]
    changed = m.rescore_fallback(rows, {"S-05": ["blocked_reason", "quick_meal"]})
    assert changed == ["S-05 agent 1"]
    assert ok["passed"] is True and ok["checks"]["fallback_plan"] is True and "trace" not in ok
    assert ok["rescored"] == ["fallback_plan"]
    assert bad_item["passed"] is False and no_reason["failed_checks"] == [
        "fallback_plan"
    ]  # still failures
    assert (
        m.rescore_fallback([ok], {"S-05": ["blocked_reason"]}) == []
    )  # passed rows are left alone
