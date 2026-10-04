from moodmeals.core.guard import Guard, RunBudget
from moodmeals.core.state import RunState


def fresh(**kw):
    s = RunState(started_at=1000.0)
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def guard(now=1000.0, **budget):
    return Guard(RunBudget(**budget), clock=lambda: now)


def test_fresh_run_is_allowed():
    assert guard().check(fresh()) is None


def test_iteration_limit():
    assert guard().check(fresh(iterations=11)) is None
    assert guard().check(fresh(iterations=12)) == "max_iterations"


def test_tool_call_limit():
    assert guard().check(fresh(tool_calls=19)) is None
    assert guard().check(fresh(tool_calls=20)) == "max_tool_calls"


def test_time_limit():
    assert guard(now=1089.9).check(fresh()) is None
    assert guard(now=1090.0).check(fresh()) == "max_seconds"


def test_cancel_flag_wins():
    assert guard().check(fresh(cancelled=True, iterations=99)) == "cancelled"


def test_fourth_question_is_refused():
    g, s = guard(), fresh()
    assert [g.record_question(s) for _ in range(4)] == [True, True, True, False]
    assert s.questions_asked == 3


def test_validation_retries_capped_at_two():
    g, s = guard(), fresh()
    assert [g.record_validation_retry(s) for _ in range(3)] == [True, True, False]


def test_custom_budget_is_respected():
    g, s = guard(max_questions=1), fresh()
    assert g.record_question(s) is True
    assert g.record_question(s) is False
