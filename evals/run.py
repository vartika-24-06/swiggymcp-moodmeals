"""Run one scenario with one strategy and return a `RunRecord` (tasks T7.2; design 13.2).

The scripted user answers from the scenario (no second model), picks the saved address,
applies mid-run changes after the plan is shown, and always stops at the approval screen:
no write is ever approved. Strategies: `agent` (the full loop), `fixed_workflow` (a code
baseline) and `one_shot` (no tools, so every entity is a guess). Scoring is in `scoring.py`.
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass, field
from typing import Any

from evals.fixed_workflow import FixedWorkflowLLM
from evals.scenario import Scenario
from moodmeals.core.actions import ProposeAction, ProtocolError, parse_action
from moodmeals.core.events import Event
from moodmeals.core.guard import Guard
from moodmeals.core.ledger import Ledger
from moodmeals.core.loop import Agent, build_plan
from moodmeals.core.state import RunState
from moodmeals.core.validator import Constraints
from moodmeals.core.view import build_model_view
from moodmeals.models.types import Plan
from moodmeals.providers import world as w
from moodmeals.providers.mock import MockProvider
from moodmeals.tools.normalise import parse_menu, parse_products, parse_restaurants

STRATEGIES = ("one_shot", "fixed_workflow", "agent")
MAX_TURNS = 25  # outer loop guard for the scripted user


@dataclass
class RunRecord:
    scenario_id: str
    strategy: str
    phase: str
    stop_reason: str | None
    outcome: dict[str, Any] | None
    plan: Plan | None
    ledger: Ledger
    constraints: Constraints
    events: list[Event]
    questions_asked: int
    tool_calls: int
    mid_run_applied: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float | None = None
    model: str = ""
    hallucinated: int | None = None  # one_shot: proposed entities not in the world
    notes: list[str] = field(default_factory=list)


def _usage(llm: Any) -> tuple[int, int, float | None, str]:
    u = llm.usage() if hasattr(llm, "usage") else None
    cost = llm.cost_estimate() if hasattr(llm, "cost_estimate") else None
    return (
        getattr(u, "tokens_in", 0),
        getattr(u, "tokens_out", 0),
        cost,
        str(getattr(llm, "model", "")),
    )


def _record(sc: Scenario, strategy: str, state: RunState, llm: Any, **kw: Any) -> RunRecord:
    tin, tout, cost, model = _usage(llm)
    return RunRecord(
        scenario_id=sc.id, strategy=strategy, phase=state.phase, stop_reason=state.stop_reason,
        outcome=state.outcome, plan=state.plan, ledger=state.ledger, constraints=state.constraints,
        events=list(state.events), questions_asked=state.questions_asked,
        tool_calls=state.tool_calls, tokens_in=tin, tokens_out=tout, cost_usd=cost, model=model,
        **kw,
    )  # fmt: skip


def _provider(sc: Scenario) -> MockProvider:
    wd = sc.world
    return MockProvider(seed=wd.seed, switches=wd.build_switches(), n_addresses=wd.addresses)


def eval_guard(llm: Any) -> Guard:
    """The run's time limit counts the agent's time, not time spent waiting for a rate limit
    or pacing calls (`paused_s` on the runner's client wrapper)."""
    return Guard(clock=lambda: time.time() - getattr(llm, "paused_s", 0.0))


def run_scripted(sc: Scenario, llm: Any, strategy: str = "agent", guard: Guard | None = None):
    """The scripted user drives the loop until the approval screen, a stop, or the end."""
    agent = Agent(llm, _provider(sc), guard or eval_guard(llm))
    state = agent.start(sc.user_script.opening, sc.user_script.hard_constraints())
    steps, applied = list(sc.user_script.mid_run), 0
    for _ in range(MAX_TURNS):
        agent.run(state)
        if state.waiting == "answer":
            field_ = (state.pending_question or {}).get("field", "other")
            agent.provide_answer(state, sc.user_script.answers.get(field_, "no preference"))
        elif state.waiting == "address":
            options = agent.address_options(state)
            agent.choose_address(state, options[sc.user_script.address]["handle"])
        elif state.phase == "AWAITING_APPROVAL" and steps:
            step = steps.pop(0)
            if step.do == "change_constraints":
                agent.change_constraints(state, **step.with_)
            else:
                agent.reject(state)
            applied += 1
        else:
            break  # approval screen (never approved), DONE or STOPPED
    return _record(sc, strategy, state, llm, mid_run_applied=applied)


# --------------------------------------------------------------------------- one_shot


def _truth_ledger(sc: Scenario) -> Ledger:
    """Every restaurant, dish and product in the scenario's world: the ground truth."""
    wd, ledger = sc.world, Ledger()
    sw = dataclasses.replace(wd.build_switches(), fail_tools={}, empty_search=False)
    world = w.build_world(wd.seed, wd.addresses)
    offset = 0
    while True:
        page = w.search_restaurants_payload(world, sw, "", offset)
        ledger.add_restaurants(parse_restaurants(page))
        if not page.get("hasMore"):
            break
        offset = page["nextOffset"]
    for rid in list(ledger.restaurants):
        for pg in (1, 2, 3):
            _, items = parse_menu(w.menu_payload(world, sw, rid, pg, 8))
            ledger.add_menu_items(items)
    ledger.add_products(parse_products(w.search_products_payload(world, sw, "", 0)))
    return ledger


def run_one_shot(sc: Scenario, llm: Any) -> RunRecord:
    """No tools: the model must propose from its own knowledge. The ids it names are then
    checked against the world, which counts hallucinated entities (design 13.2)."""
    truth = _truth_ledger(sc)
    state = RunState(
        mode="mock", user_text=sc.user_script.opening, constraints=sc.user_script.hard_constraints()
    )
    state.add_event("user_input", "user", {"text": sc.user_script.opening})
    view = build_model_view(state)
    view["available_tools"] = {}
    view["limits_left"] = {**view["limits_left"], "questions": 0, "tool_calls": 0}
    view["notes"] = ["No tools are available. Propose a plan now from your own knowledge."]
    hallucinated, notes = None, []
    try:
        action = parse_action(llm.next_action(view))
    except ProtocolError as e:
        action = None
        notes.append(f"protocol_error: {e}")
    except Exception as e:  # noqa: BLE001  (a model error is a failed run, not a crash)
        action = None
        notes.append(f"model_error: {type(e).__name__}")
    if isinstance(action, ProposeAction):
        unknown = [
            i["id"] for i in action.items if i["id"] not in {**truth.menu_items, **truth.products}
        ]
        if action.path == "order_in" and action.restaurant_id not in truth.restaurants:
            unknown.append(str(action.restaurant_id))
        hallucinated = len(unknown)
        state.ledger = truth
        if not unknown:
            try:
                state.plan = build_plan(action, state)
            except Exception as e:  # noqa: BLE001
                notes.append(f"plan_error: {type(e).__name__}")
        state.add_event(
            "validation", "code", {"ok": not unknown, "errors": ["unknown_entity"] * len(unknown)}
        )
        if state.plan is not None:
            state.add_event("plan", "model", state.plan.model_dump(), rationale=action.rationale)
    elif action is not None:
        notes.append("not_a_plan")
    state.phase = "AWAITING_APPROVAL" if state.plan is not None else "STOPPED"
    state.stop_reason = None if state.plan is not None else "no_plan"
    return _record(sc, "one_shot", state, llm, hallucinated=hallucinated or 0, notes=notes)


def run_strategy(name: str, sc: Scenario, llm: Any = None) -> RunRecord:
    if name == "fixed_workflow":
        return run_scripted(sc, FixedWorkflowLLM(), "fixed_workflow")
    if llm is None:
        raise ValueError(f"strategy {name} needs a model client")
    if name == "agent":
        return run_scripted(sc, llm, "agent")
    if name == "one_shot":
        return run_one_shot(sc, llm)
    raise ValueError(f"unknown strategy {name}; use one of {', '.join(STRATEGIES)}")
