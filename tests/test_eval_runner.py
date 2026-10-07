# ruff: noqa: E501
"""The eval runner: results file, spend cap, key handling (tasks T7.3). No network, no real model."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals import runner  # noqa: E402
from evals.scenario import load_scenarios  # noqa: E402
from moodmeals.models.adapters import Usage  # noqa: E402
from moodmeals.models.demo import DemoLLM  # noqa: E402

SMOKE = load_scenarios(smoke_only=True)
PER_CALL_USD = 0.01


class CostlyLLM(DemoLLM):
    """The scripted demo, but every model call costs a fixed amount (for the cap tests)."""

    provider, model = "openai", "gpt-5-mini"
    total_calls = 0  # across every instance: the cap must hold over a whole eval

    def next_action(self, view):
        CostlyLLM.total_calls += 1
        return super().next_action(view)

    def usage(self):
        return Usage(calls=self._calls, tokens_in=100 * self._calls, tokens_out=10 * self._calls)

    def cost_estimate(self):
        return self._calls * PER_CALL_USD


@pytest.fixture(autouse=True)
def _reset_calls():
    CostlyLLM.total_calls = 0


def test_a_normal_run_writes_a_complete_results_document(tmp_path):
    doc = runner.run_eval(
        SMOKE, ["agent", "fixed_workflow"], DemoLLM, meta={"model": "scripted-demo"}
    )
    path = runner.write_results(doc, tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == f"{doc['date']}-scripted-demo.json"
    for key in ("version", "date", "strategies", "runs_per_scenario", "cap_inr", "spent_inr",
                "aborted_by_cap", "summary", "results", "usd_inr", "prices_checked"):  # fmt: skip
        assert key in data, key
    assert data["n_runs"] == len(data["results"]) == 12 and data["aborted_by_cap"] is False
    assert data["summary"]["agent"]["all_checks"]["text"].endswith(" of 6")  # "k of n"


def test_failures_are_published_with_a_trace_and_passes_are_not(tmp_path):
    doc = runner.run_eval(SMOKE, ["fixed_workflow"], None)
    failed = [r for r in doc["results"] if not r["passed"]]
    passed = [r for r in doc["results"] if r["passed"]]
    assert failed and passed  # the baseline fails some scenarios by design
    assert all(r["trace"] and r["failed_checks"] for r in failed)
    assert all("trace" not in r for r in passed)
    json.dumps(doc)  # everything is plain JSON


def test_deterministic_strategies_run_once_and_model_strategies_run_n_times():
    doc = runner.run_eval(SMOKE[:1], ["agent", "fixed_workflow"], DemoLLM, runs=3)
    by = [(r["strategy"], r["run"]) for r in doc["results"]]
    assert by.count(("agent", 1)) == by.count(("agent", 3)) == 1
    assert [s for s, _ in by].count("fixed_workflow") == 1


def test_fixed_workflow_alone_needs_no_model_client():
    doc = runner.run_eval(SMOKE, ["fixed_workflow"], None)
    assert doc["n_runs"] == 6 and doc["spent_inr"] == 0


# ---------------------------------------------------------------- the spend cap


def test_the_cap_stops_the_eval_and_the_results_are_still_written(tmp_path):
    cap_inr, usd_inr = 5.0, 100.0  # a cap of $0.05 = five calls
    doc = runner.run_eval(SMOKE, ["agent"], CostlyLLM, cap_inr=cap_inr, usd_inr=usd_inr)
    assert doc["aborted_by_cap"] is True and doc["aborted_run"] is not None
    one_call_inr = PER_CALL_USD * usd_inr
    assert CostlyLLM.total_calls * one_call_inr <= cap_inr + one_call_inr  # at most one over
    assert doc["spent_inr"] <= cap_inr + one_call_inr
    aborted = (doc["aborted_run"]["scenario_id"], doc["aborted_run"]["strategy"])
    assert aborted not in {(r["scenario_id"], r["strategy"]) for r in doc["results"]}  # not scored
    done = {r["scenario_id"] for r in doc["results"]}
    assert len(done) < len(SMOKE)  # later scenarios never started
    path = runner.write_results(doc, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["aborted_by_cap"] is True


def test_a_cap_of_zero_calls_makes_no_model_call():
    doc = runner.run_eval(SMOKE, ["agent"], CostlyLLM, cap_inr=0.0001, usd_inr=100.0)
    assert CostlyLLM.total_calls <= 1 and doc["aborted_by_cap"] is True


def test_free_models_never_trip_the_cap():
    doc = runner.run_eval(SMOKE, ["agent"], DemoLLM, cap_inr=1.0)
    assert doc["aborted_by_cap"] is False and doc["spent_inr"] == 0


def test_spend_meter_counts_finished_runs_plus_the_live_one():
    m = runner.SpendMeter(cap_inr=10, usd_inr=100)  # $0.10
    m.done_usd = 0.06
    live = CostlyLLM()
    assert not m.exhausted(live)
    live._calls = 4  # +$0.04
    assert m.exhausted(live) and m.done_inr == pytest.approx(6.0)


def test_capped_client_refuses_before_calling_the_model():
    meter = runner.SpendMeter(cap_inr=1, usd_inr=100)  # $0.01
    inner = CostlyLLM()
    inner._calls = 1
    client = runner.CappedClient(inner, meter)
    with pytest.raises(runner.SpendCapReached):
        client.next_action({})
    assert meter.hit and CostlyLLM.total_calls == 0 and client.model == "gpt-5-mini"


# ---------------------------------------------------------------- files


def test_results_are_never_overwritten_and_names_are_safe(tmp_path):
    doc = {"date": "2026-10-07", "model": "openai/gpt-oss-20b"}
    a = runner.write_results(doc, tmp_path)
    b = runner.write_results(doc, tmp_path)
    assert a.name == "2026-10-07-openai-gpt-oss-20b.json" and b.name.endswith("-2.json")
    assert a.exists() and b.exists()


# ---------------------------------------------------------------- command line


def test_main_runs_the_demo_and_writes_a_file(tmp_path, capsys):
    code = runner.main(["--set", "smoke", "--out", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0 and "Plan: 6 scenarios" in out and "Wrote" in out
    [path] = list(tmp_path.glob("*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["prompt_version"] == "agent_v4" and data["git"]["commit"]
    assert data["provider"] == "demo" and "temperature" in data


def test_dry_plan_prints_the_estimate_and_runs_nothing(tmp_path, capsys):
    assert runner.main(["--dry-plan", "--out", str(tmp_path)]) == 0
    assert "Rough cost estimate" in capsys.readouterr().out and not list(tmp_path.iterdir())


def test_a_real_provider_needs_its_key_and_a_known_price(tmp_path, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(SystemExit, match="GROQ_API_KEY"):
        runner.main(["--provider", "groq", "--model", "m", "--out", str(tmp_path)])
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    with pytest.raises(SystemExit, match="No price"):
        runner.main(["--provider", "openai", "--model", "unpriced", "--out", str(tmp_path)])
    with pytest.raises(SystemExit, match="Unknown strategy"):
        runner.main(["--strategies", "telepathy", "--out", str(tmp_path)])


def test_the_key_is_never_printed_or_saved(tmp_path, monkeypatch, capsys):
    secret = "sk-SENTINEL-do-not-leak-123"
    monkeypatch.setenv("OPENAI_API_KEY", secret)
    monkeypatch.setattr(runner, "make_client", lambda provider, model, key: CostlyLLM())
    code = runner.main(["--provider", "openai", "--model", "gpt-5-mini", "--cap-inr", "100000",
                        "--out", str(tmp_path)])  # fmt: skip
    text = capsys.readouterr().out + "".join(
        p.read_text(encoding="utf-8") for p in tmp_path.glob("*")
    )
    assert code == 0 and secret not in text


def test_main_exits_with_3_when_the_cap_stops_it_and_still_writes_results(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(runner, "make_client", lambda provider, model, key: CostlyLLM())
    code = runner.main(["--provider", "openai", "--model", "gpt-5-mini", "--strategies", "agent",
                        "--cap-inr", "1", "--usd-inr", "100", "--out", str(tmp_path)])  # fmt: skip
    assert code == 3 and "STOPPED by the spend cap" in capsys.readouterr().out
    [path] = list(tmp_path.glob("*.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["aborted_by_cap"] is True


def test_git_info_never_raises():
    info = runner.git_info()
    assert isinstance(info["commit"], str) and info["commit"]


# ---------------------------------------------------------------- rate limits and invalid runs


class Flaky:
    """A client whose first `fail_first` calls raise the given error."""

    provider, model = "groq", "m"

    def __init__(self, fail_first, error="rate_limited"):
        self.fail_first, self.error, self.calls = fail_first, error, 0

    def next_action(self, view):
        self.calls += 1
        if self.calls <= self.fail_first:
            raise runner.LLMError(self.error)
        return {"action": "ask_user", "args": {"question": "q", "field": "diet"}, "rationale": ""}

    def usage(self):
        return Usage(calls=self.calls)

    def cost_estimate(self):
        return 0.0


def test_rate_limits_are_waited_out_with_a_growing_pause_then_retried():
    sleeps = []
    c = runner.RetryingClient(Flaky(2), retries=3, wait_s=10, sleep=sleeps.append)
    assert c.next_action({})["action"] == "ask_user"
    assert sleeps == [10, 20] and c.model == "m"


def test_after_the_retries_a_rate_limit_is_raised_and_other_errors_are_not_retried():
    sleeps = []
    c = runner.RetryingClient(Flaky(99), retries=2, wait_s=1, sleep=sleeps.append)
    with pytest.raises(runner.LLMError, match="rate_limited"):
        c.next_action({})
    assert len(sleeps) == 2 and c._inner.calls == 3
    bad_key = runner.RetryingClient(Flaky(99, "http 401"), retries=3, wait_s=1, sleep=sleeps.append)
    with pytest.raises(runner.LLMError):
        bad_key.next_action({})
    assert bad_key._inner.calls == 1  # a bad key is not retried


def test_calls_are_paced_at_the_minimum_interval():
    t, sleeps = [100.0], []

    def sleep(s):
        sleeps.append(s)
        t[0] += s

    c = runner.RetryingClient(Flaky(0), min_interval_s=20, sleep=sleep, clock=lambda: t[0])
    c.next_action({})
    t[0] += 5  # 5 s later
    c.next_action({})
    assert sleeps == [15]  # waits only the remaining 15 s


def test_unreachable_model_runs_are_invalid_not_agent_failures():
    doc = runner.run_eval(SMOKE[:2], ["agent", "fixed_workflow"], lambda: Flaky(99))
    agent_rows = [r for r in doc["results"] if r["strategy"] == "agent"]
    assert agent_rows and all(
        r["invalid"] and "rate_limited" in r["invalid_reason"] for r in agent_rows
    )
    assert doc["invalid_runs"] == len(agent_rows)
    assert "agent" not in doc["summary"]  # nothing was scored for the agent
    fixed = [r for r in doc["results"] if r["strategy"] == "fixed_workflow"]
    assert fixed and all("invalid" not in r for r in fixed)  # the baseline is unaffected


def test_several_invalid_runs_in_a_row_stop_the_eval_as_unreachable():
    doc = runner.run_eval(SMOKE, ["agent"], lambda: Flaky(99))
    assert (
        doc["aborted_unreachable"] is True and doc["invalid_runs"] == runner.MAX_CONSECUTIVE_INVALID
    )
    assert doc["n_runs"] == runner.MAX_CONSECUTIVE_INVALID  # it did not grind through all six


def test_a_good_run_resets_the_unreachable_counter():
    clients = iter([Flaky(99), Flaky(99), DemoLLM(), Flaky(99), Flaky(99), DemoLLM()])
    doc = runner.run_eval(SMOKE, ["agent"], lambda: next(clients))
    assert doc["aborted_unreachable"] is False and doc["invalid_runs"] == 4


def test_main_exits_with_4_when_the_model_is_unreachable(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr(runner, "make_client", lambda provider, model, key: Flaky(99))
    code = runner.main(["--provider", "groq", "--model", "qwen/qwen3.8-27b", "--strategies", "agent",
                        "--min-interval-s", "0", "--rate-limit-wait-s", "0", "--out", str(tmp_path)])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 4 and "INVALID" in out and "failed to answer several runs" in out
    [path] = list(tmp_path.glob("*.json"))
    assert json.loads(path.read_text(encoding="utf-8"))["aborted_unreachable"] is True


def test_groq_is_paced_by_default_and_others_are_not(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    monkeypatch.setattr(runner, "make_client", lambda provider, model, key: Flaky(99))
    runner.main(["--provider", "groq", "--model", "m", "--strategies", "agent", "--rate-limit-wait-s", "0",
                 "--rate-limit-retries", "0", "--out", str(tmp_path), "--set", "smoke"])  # fmt: skip
    # the first call of each client is never delayed, so this finishes at once even when paced
    assert "Pacing model calls at least 20s apart" in capsys.readouterr().out
    assert runner.DEFAULT_MIN_INTERVAL_S == {"groq": 20.0}
