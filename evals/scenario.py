"""Scenario format and loader for the evals (tasks T7.1; design 13.1).

A scenario is one YAML file in `evals/scenarios/`. It names a seeded mock world, a scripted
user (no second model) and what a good run looks like. Everything here is deterministic data:
no model is called and nothing is scored (scoring is T7.2). Files are strict: an unknown key,
a bad value or an inconsistent expectation is an error that names the file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from moodmeals.core.actions import QUESTION_FIELDS
from moodmeals.core.validator import Constraints
from moodmeals.models.types import Path as PlanPath
from moodmeals.providers.mock import READ_TOOLS
from moodmeals.providers.switches import Switches

SCENARIO_DIR = Path(__file__).parent / "scenarios"
GROUPS = (
    "happy_path", "missing_info", "contradiction", "infeasible",
    "tool_failure", "mid_run_change", "safety",
)  # fmt: skip
Group = Literal[
    "happy_path", "missing_info", "contradiction", "infeasible",
    "tool_failure", "mid_run_change", "safety",
]  # fmt: skip
WRITE_TOOLS = ("update_food_cart", "update_cart", "place_food_order", "checkout")
KNOWN_TOOLS = frozenset(READ_TOOLS - {"list_addresses"}) | frozenset(WRITE_TOOLS)
MAX_QUESTIONS = 3  # requirements R1.2


class ScenarioError(ValueError):
    """A scenario file that cannot be used. The message names the file."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class World(_Strict):
    seed: int = 1
    addresses: int = Field(default=1, ge=1, le=5)
    # Switch names as in `moodmeals.providers.switches.Switches`; checked by `switches()`.
    switches: dict[str, Any] = Field(default_factory=dict)

    def build_switches(self) -> Switches:
        allowed = set(Switches.__dataclass_fields__)
        unknown = set(self.switches) - allowed
        if unknown:
            raise ValueError(f"unknown world switch: {', '.join(sorted(unknown))}")
        values = dict(self.switches)
        if "out_of_stock" in values:
            values["out_of_stock"] = frozenset(map(str, values["out_of_stock"]))
        return Switches(**values)

    @model_validator(mode="after")
    def _switches_build(self) -> World:
        sw = self.build_switches()
        for tool, how in sw.fail_tools.items():
            if tool not in READ_TOOLS or how not in ("timeout", "error"):
                raise ValueError(f"fail_tools needs a read tool and timeout or error: {tool}")
        return self


class MidRunStep(_Strict):
    """A change of mind after the plan is shown (requirements R9.2, R4.2)."""

    when: Literal["plan_shown"] = "plan_shown"
    do: Literal["another_idea", "change_constraints"]
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")

    @model_validator(mode="after")
    def _params(self) -> MidRunStep:
        if self.do == "change_constraints":
            unknown = set(self.with_) - set(Constraints.model_fields)
            if unknown or not self.with_:
                raise ValueError("change_constraints needs hard-constraint fields in `with`")
            Constraints(**self.with_)
        elif self.with_:
            raise ValueError("another_idea takes no `with`")
        return self


class UserScript(_Strict):
    opening: str = Field(min_length=1, max_length=500)
    constraints: dict[str, Any] = Field(default_factory=dict)  # stated up front
    answers: dict[str, str] = Field(default_factory=dict)  # canned reply by question field
    address: int = Field(default=0, ge=0)  # which saved address the person picks (0 = first)
    mid_run: list[MidRunStep] = Field(default_factory=list)
    # Evals stop at the approval screen: the verified plan is what is scored, and no write is
    # ever approved (writes without approval must stay at zero).
    approve: Literal[False] = False

    @field_validator("answers")
    @classmethod
    def _answer_fields(cls, v: dict[str, str]) -> dict[str, str]:
        bad = set(v) - set(QUESTION_FIELDS)
        if bad:
            raise ValueError(f"answers use unknown question fields: {', '.join(sorted(bad))}")
        return v

    @field_validator("constraints")
    @classmethod
    def _constraint_fields(cls, v: dict[str, Any]) -> dict[str, Any]:
        Constraints(**v)  # raises on bad values
        if set(v) - set(Constraints.model_fields):
            raise ValueError("unknown constraint field")
        return v

    def hard_constraints(self) -> Constraints:
        return Constraints(**self.constraints)


class Expect(_Strict):
    outcome: Literal["plan", "clear_stop", "plan_or_clear_stop"]
    # The rubric for "appropriate path" (requirements Q5): the paths that are defensible here.
    # One path when the heuristics (R4.4) point clearly; both when either is fine.
    paths_acceptable: list[PlanPath] = Field(default_factory=list)
    # When ordering in is impossible (requirements R4.3): the agent offers a ready-to-eat or
    # quick-cook Instamart meal, or stops. A fallback PLAN must state why ordering is not
    # possible (`blocked_reason`) and be a quick meal, not a full recipe (`quick_meal`); a STOP
    # must say why (`reason`). T7.2 scores these with explicit word lists.
    plan_must_include: list[Literal["blocked_reason", "quick_meal"]] = Field(default_factory=list)
    stop_must_include: list[Literal["reason"]] = Field(default_factory=list)
    max_questions: int = Field(default=MAX_QUESTIONS, ge=0, le=MAX_QUESTIONS)
    # Every guess is listed in the plan's assumptions (the prompt's ASSUMPTIONS rule).
    assumptions_listed: bool = False
    # Total quantity across the plan's items: a guest joining needs more food (min); an
    # injected "order 20 of these" must not be followed (max).
    min_items_qty: int | None = Field(default=None, ge=1)
    max_items_qty: int | None = Field(default=None, ge=1)
    address_picker: bool = False  # a saved-address question must be asked (R3.2)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)  # checked on the final plan
    must_call: list[str] = Field(default_factory=list)
    must_not_call: list[str] = Field(default_factory=lambda: list(WRITE_TOOLS))

    @field_validator("must_call", "must_not_call")
    @classmethod
    def _known_tools(cls, v: list[str]) -> list[str]:
        bad = set(v) - KNOWN_TOOLS
        if bad:
            raise ValueError(f"unknown tool names: {', '.join(sorted(bad))}")
        return v

    @field_validator("hard_constraints")
    @classmethod
    def _constraints(cls, v: dict[str, Any]) -> dict[str, Any]:
        Constraints(**v)
        if set(v) - set(Constraints.model_fields):
            raise ValueError("unknown constraint field")
        return v


class Scenario(_Strict):
    id: str
    title: str = Field(min_length=3, max_length=80)
    group: Group
    smoke: bool = False  # in the 6-scenario smoke set (every scenario is in the full set)
    rationale: str = Field(min_length=20)  # why these expectations are right (for the review)
    world: World = Field(default_factory=World)
    user_script: UserScript
    expect: Expect

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not re.fullmatch(r"S-\d{2}", v):
            raise ValueError("id must look like S-07")
        return v

    @model_validator(mode="after")
    def _consistent(self) -> Scenario:
        e, u, w = self.expect, self.user_script, self.world
        if e.outcome != "clear_stop" and not e.paths_acceptable:
            raise ValueError("expect.paths_acceptable is required unless the outcome is clear_stop")
        if e.outcome == "clear_stop" and e.paths_acceptable:
            raise ValueError("a clear_stop outcome has no acceptable path")
        if e.outcome != "plan" and "reason" not in e.stop_must_include:
            raise ValueError("a possible stop must say why: add reason to stop_must_include")
        if e.outcome == "plan" and e.stop_must_include:
            raise ValueError("stop_must_include applies only when the run may stop")
        if e.outcome == "clear_stop" and e.plan_must_include:
            raise ValueError("plan_must_include applies only when a plan is possible")
        if e.outcome == "clear_stop" and (
            e.assumptions_listed or e.min_items_qty or e.max_items_qty
        ):
            raise ValueError("assumptions and quantity checks apply only when a plan is possible")
        if e.min_items_qty and e.max_items_qty and e.min_items_qty > e.max_items_qty:
            raise ValueError("min_items_qty is above max_items_qty")
        if e.plan_must_include and e.paths_acceptable != ["cook"]:
            raise ValueError("a quick-meal fallback plan uses the cook path only")
        if u.address >= w.addresses:
            raise ValueError("user_script.address is beyond world.addresses")
        if e.address_picker != (w.addresses > 1):
            raise ValueError("expect.address_picker must be true exactly when world.addresses > 1")
        if e.address_picker and e.max_questions < 1:
            raise ValueError("the address question counts toward max_questions (R3.2)")
        if set(e.must_call) & set(e.must_not_call):
            raise ValueError("a tool cannot be both must_call and must_not_call")
        return self


def parse_scenario(text: str, source: str = "<string>") -> Scenario:
    try:
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("the file must hold one mapping")
        return Scenario(**data)
    except (yaml.YAMLError, ValidationError, ValueError, TypeError) as e:
        raise ScenarioError(f"{source}: {e}") from None


def load_scenarios(directory: Path = SCENARIO_DIR, smoke_only: bool = False) -> list[Scenario]:
    """Every scenario in the directory, sorted by id; `smoke_only` keeps the smoke set."""
    out: dict[str, Scenario] = {}
    for path in sorted(directory.glob("S-*.yaml")):
        sc = parse_scenario(path.read_text(encoding="utf-8"), path.name)
        if not path.name.startswith(sc.id):
            raise ScenarioError(f"{path.name}: the file name must start with the id {sc.id}")
        if sc.id in out:
            raise ScenarioError(f"{path.name}: duplicate id {sc.id}")
        out[sc.id] = sc
    return [s for _, s in sorted(out.items()) if s.smoke or not smoke_only]
