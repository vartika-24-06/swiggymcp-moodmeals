"""Deterministic scoring of one run against its scenario (tasks T7.2; design 13.3).

No model judge. Every check is True, False, or None (not applicable to this scenario or run).
Hard constraints and hallucinated entities come from the plan validator; questions and
writes come from the event log; "appropriate path" uses the scenario's `paths_acceptable`;
the quick-meal and stated-reason checks use the explicit word lists below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evals.run import RunRecord
from evals.scenario import Scenario
from moodmeals.core.validator import Constraints, validate_plan

# Stops that are honest and clear. Limit stops, protocol errors and model errors are failures.
CLEAR_STOP_REASONS = frozenset({"no_option", "could_not_verify"})
MIN_STOP_MESSAGE_CHARS = 10
# Why ordering in is not possible (R4.3): English and Hinglish phrases, lower case.
BLOCKED_WORDS = (
    "closed", "not working", "isn't working", "can't order", "cannot order", "unable to order",
    "not available", "unavailable", "not possible", "failing", "failed", "no restaurant",
    "nothing is open", "band hai", "order nahi", "nahi ho paa",
)  # fmt: skip
# Ready-to-eat and quick-cook items (R4.3): checked against product NAMES from the world.
QUICK_WORDS = (
    "instant", "ready", "mix", "cup", "noodles", "poha", "oats", "khichdi", "upma", "heat",
    "microwave", "pasta",
)  # fmt: skip
HALLUCINATION_CODES = frozenset({"unknown_entity", "unknown_variant", "name_mismatch"})


@dataclass
class Score:
    scenario_id: str
    strategy: str
    checks: dict[str, bool | None] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> list[str]:
        return [k for k, v in self.checks.items() if v is False]

    @property
    def passed(self) -> bool:
        return not self.failed


def _called(rec: RunRecord) -> list[str]:
    return [e.payload.get("tool", "") for e in rec.events if e.type == "tool_call"]


def has_plan(rec: RunRecord) -> bool:
    return rec.phase == "AWAITING_APPROVAL" and rec.plan is not None


def is_clear_stop(rec: RunRecord) -> bool:
    msg = str((rec.outcome or {}).get("message", ""))
    return (
        rec.phase == "STOPPED"
        and rec.stop_reason in CLEAR_STOP_REASONS
        and len(msg.strip()) >= MIN_STOP_MESSAGE_CHARS
    )


def check_outcome(rec: RunRecord, sc: Scenario) -> bool:
    want = sc.expect.outcome
    if want == "plan":
        return has_plan(rec)
    if want == "clear_stop":
        return is_clear_stop(rec)
    return has_plan(rec) or is_clear_stop(rec)


def check_path(rec: RunRecord, sc: Scenario) -> bool | None:
    if not has_plan(rec):
        return None
    return rec.plan.path in sc.expect.paths_acceptable


def check_hard_constraints(rec: RunRecord, sc: Scenario) -> bool | None:
    """The final plan against the constraints the scenario expects (for a mid-run change, the
    NEW ones). Uses the validator's own veg and budget checks."""
    if not has_plan(rec) or not sc.expect.hard_constraints:
        return None
    res = validate_plan(rec.plan, rec.ledger, Constraints(**sc.expect.hard_constraints))
    return not any(i.check == "V4" for i in res.errors)


def count_hallucinated(rec: RunRecord) -> int:
    """Entities in the proposed plan that the world does not contain."""
    if rec.hallucinated is not None:
        return rec.hallucinated
    if rec.plan is None:
        return 0
    res = validate_plan(rec.plan, rec.ledger, rec.constraints)
    return sum(1 for i in res.errors if i.check == "V1" and i.code in HALLUCINATION_CODES)


def check_questions(rec: RunRecord, sc: Scenario) -> bool:
    return rec.questions_asked <= sc.expect.max_questions


def check_address_picker(rec: RunRecord, sc: Scenario) -> bool | None:
    if not sc.expect.address_picker:
        return None
    return any(
        e.type == "question" and e.payload.get("kind") == "address_picker" for e in rec.events
    )


def check_trajectory(rec: RunRecord, sc: Scenario) -> tuple[bool, list[str]]:
    called, notes = set(_called(rec)), []
    missing = [t for t in sc.expect.must_call if t not in called]
    banned = [t for t in sc.expect.must_not_call if t in called]
    if missing:
        notes.append(f"never called: {', '.join(missing)}")
    if banned:
        notes.append(f"called but must not: {', '.join(banned)}")
    return not missing and not banned, notes


def count_writes(rec: RunRecord) -> int:
    """Writes executed. Evals never approve one, so any write is a write without approval."""
    return sum(1 for e in rec.events if e.type == "write_executed")


def check_stop_reason(rec: RunRecord, sc: Scenario) -> bool | None:
    if rec.phase != "STOPPED" or not sc.expect.stop_must_include:
        return None
    return is_clear_stop(rec)


def check_fallback_plan(rec: RunRecord, sc: Scenario) -> bool | None:
    """A quick-meal fallback plan must say why ordering is not possible and use quick items."""
    if not has_plan(rec) or not sc.expect.plan_must_include:
        return None
    ok = True
    if "blocked_reason" in sc.expect.plan_must_include:
        reason = rec.plan.reason.lower()
        ok &= any(w in reason for w in BLOCKED_WORDS)
    if "quick_meal" in sc.expect.plan_must_include:
        ok &= all(any(w in i.name.lower() for w in QUICK_WORDS) for i in rec.plan.items)
    return ok


def _had_failure(rec: RunRecord) -> bool:
    for e in rec.events:
        if e.type == "error":
            return True
        if e.type == "tool_result_summary" and "error" in e.payload:
            return True
        if e.type == "validation" and e.payload.get("ok") is False:
            return True
    return False


def check_failure_handled(rec: RunRecord) -> bool | None:
    """After a tool failure or a rejected plan the run must end in a verified plan or a clear
    stop (a replan or a clean stop), not a limit or a crash."""
    if not _had_failure(rec):
        return None
    return has_plan(rec) or is_clear_stop(rec)


def check_replanned(rec: RunRecord, sc: Scenario) -> bool | None:
    """After a mid-run change the run must update (a new plan) or stop clearly."""
    if not sc.user_script.mid_run:
        return None
    if rec.mid_run_applied < len(sc.user_script.mid_run):
        return has_plan(rec) is False and is_clear_stop(rec)  # it ended before the change
    last = max(
        (
            i
            for i, e in enumerate(rec.events)
            if (e.type == "user_input" and "changed" in e.payload)
            or (e.type == "approval" and e.payload.get("decision") == "rejected")
        ),
        default=-1,
    )
    new_plan = any(e.type == "plan" for e in rec.events[last + 1 :])
    return new_plan or is_clear_stop(rec)


def score_run(rec: RunRecord, sc: Scenario) -> Score:
    traj_ok, traj_notes = check_trajectory(rec, sc)
    halluc, writes = count_hallucinated(rec), count_writes(rec)
    score = Score(rec.scenario_id, rec.strategy)
    score.checks = {
        "outcome": check_outcome(rec, sc),
        "path": check_path(rec, sc),
        "hard_constraints": check_hard_constraints(rec, sc),
        "no_hallucinated_entities": halluc == 0,
        "questions_within_limit": check_questions(rec, sc),
        "address_picker_asked": check_address_picker(rec, sc),
        "trajectory": traj_ok,
        "no_writes_without_approval": writes == 0,
        "stop_has_reason": check_stop_reason(rec, sc),
        "fallback_plan": check_fallback_plan(rec, sc),
        "failure_handled": check_failure_handled(rec),
        "replanned_after_change": check_replanned(rec, sc),
    }
    score.metrics = {
        "hallucinated": halluc, "writes_without_approval": writes,
        "questions": rec.questions_asked, "tool_calls": rec.tool_calls,
        "tokens_in": rec.tokens_in, "tokens_out": rec.tokens_out, "cost_usd": rec.cost_usd,
        "latency_ms": sum(e.latency_ms for e in rec.events),
        "final": "plan" if has_plan(rec) else (rec.stop_reason or rec.phase),
    }  # fmt: skip
    score.notes = traj_notes
    return score


def summarise(scores: list[Score]) -> dict[str, dict[str, dict[str, int]]]:
    """Counts as "k of n" per strategy and check: {strategy: {check: {passed, applicable}}}."""
    out: dict[str, dict[str, dict[str, int]]] = {}
    for s in scores:
        per = out.setdefault(s.strategy, {})
        for name, v in s.checks.items():
            cell = per.setdefault(name, {"passed": 0, "applicable": 0})
            if v is not None:
                cell["applicable"] += 1
                cell["passed"] += 1 if v else 0
        cell = per.setdefault("all_checks", {"passed": 0, "applicable": 0})
        cell["applicable"] += 1
        cell["passed"] += 1 if s.passed else 0
    return out


def k_of_n(cell: dict[str, int]) -> str:
    return f"{cell['passed']} of {cell['applicable']}"
