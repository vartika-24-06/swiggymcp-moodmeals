"""The results dashboard reads the committed results file (T7.6)."""

from __future__ import annotations

from pathlib import Path

from evals import report

RESULTS = Path(__file__).resolve().parents[1] / "evals" / "results"


def committed():
    files = report.find_results(RESULTS)
    assert files and not any(p.name.endswith(".partial.json") for p in files)
    return next(report.load_results(p) for p in files if "full" in p.name)


def test_counts_are_k_of_n_and_match_the_run_rows():
    doc = committed()
    for strat, (k, n) in report.pass_counts(doc).items():
        rows = [r for r in doc["results"] if r["strategy"] == strat]
        assert n == len(rows) and k == sum(r["passed"] for r in rows)


def test_every_failure_is_listed_and_groups_add_up():
    doc = committed()
    assert len(report.failures(doc)) == sum(not r["passed"] for r in doc["results"])
    total = sum(n for g in report.by_group(doc).values() for _, n in g.values())
    assert total == len(doc["results"])


def test_check_table_and_cost_summary_have_every_strategy():
    doc = committed()
    row = report.check_table(doc, ("all_checks",))[0]
    assert all(label in row for label in report.STRATEGY_LABELS.values())
    assert set(report.cost_summary(doc)) == set(report.pass_counts(doc))
