"""Read a results file for the dashboard page (task T7.6). Pure functions, no Streamlit.

Counts stay "k of n" (requirement E1, E3): no percentages with false precision, failures shown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STRATEGY_LABELS = {
    "agent": "Agent (with validator)",
    "agent_no_validator": "Agent, validator off",
    "fixed_workflow": "Fixed workflow (code only)",
}


def load_results(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_results(folder: Path) -> list[Path]:
    """Top-level results files, merged full runs first, then newest name first. Progress files
    and sub-folders are skipped."""
    files = [p for p in folder.glob("*.json") if not p.name.endswith(".partial.json")]
    return sorted(files, key=lambda p: ("full" not in p.name, tuple(-ord(c) for c in p.name)))


def check_table(doc: dict[str, Any], checks: tuple[str, ...] | None = None) -> list[dict[str, str]]:
    """One row per check, one column per strategy, each cell "k of n"."""
    summary = doc.get("summary", {})
    names = checks or tuple(next(iter(summary.values()), {}))
    rows = []
    for c in names:
        row = {"check": c.replace("_", " ")}
        for strat, label in STRATEGY_LABELS.items():
            cell = summary.get(strat, {}).get(c)
            if cell is not None:
                row[label] = cell["text"] if cell["applicable"] else "n/a"
        rows.append(row)
    return rows


def pass_counts(doc: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """strategy -> (runs passing every check, runs scored)."""
    out = {}
    for strat, checks in doc.get("summary", {}).items():
        c = checks.get("all_checks")
        if c:
            out[strat] = (c["passed"], c["applicable"])
    return out


def by_group(doc: dict[str, Any]) -> dict[str, dict[str, tuple[int, int]]]:
    """scenario group -> strategy -> (runs passing every check, runs scored)."""
    out: dict[str, dict[str, list[int]]] = {}
    for r in doc.get("results", []):
        cell = out.setdefault(r["group"], {}).setdefault(r["strategy"], [0, 0])
        cell[0] += bool(r["passed"])
        cell[1] += 1
    return {g: {s: (a, b) for s, (a, b) in v.items()} for g, v in out.items()}


def failures(doc: dict[str, Any], titles: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Every failed run, with what failed. `titles` maps scenario id to its title."""
    titles = titles or {}
    rows = []
    for r in doc.get("results", []):
        if r["passed"]:
            continue
        m = r.get("metrics", {})
        rows.append(
            {
                "scenario": f"{r['scenario_id']} {titles.get(r['scenario_id'], '')}".strip(),
                "strategy": STRATEGY_LABELS.get(r["strategy"], r["strategy"]),
                "run": r["run"],
                "failed checks": ", ".join(r["failed_checks"]),
                "ended with": m.get("final", ""),
                "notes": "; ".join(r.get("notes", [])),
            }
        )
    return rows


def cost_summary(doc: dict[str, Any]) -> dict[str, float]:
    """Totals per strategy: runs, mean tool calls, mean latency (s) and model cost in rupees."""
    usd_inr = float(doc.get("usd_inr") or 0)
    out: dict[str, dict[str, float]] = {}
    for r in doc.get("results", []):
        m = r.get("metrics", {})
        s = out.setdefault(r["strategy"], {"runs": 0, "calls": 0, "ms": 0, "usd": 0.0})
        s["runs"] += 1
        s["calls"] += m.get("tool_calls", 0)
        s["ms"] += m.get("latency_ms", 0)
        s["usd"] += m.get("cost_usd", 0.0) or 0.0
    return {
        k: {
            "runs": v["runs"],
            "mean tool calls": round(v["calls"] / v["runs"], 1),
            "mean seconds": round(v["ms"] / v["runs"] / 1000, 1),
            "cost per run (₹)": round(v["usd"] / v["runs"] * usd_inr, 2),
        }
        for k, v in out.items()
    }
