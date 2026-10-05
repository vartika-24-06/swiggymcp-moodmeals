"""The agent loop: a resumable state machine over `RunState` (design.md 4; tasks T3.1-T3.3).

The model decides what to try next; code enforces everything else: limits, tool
parameters, address handling, validation, approval and writes. `run_until_pause` runs
steps until the person is needed (a question, an address pick or an approval) or the
run ends, then returns. The UI calls `provide_answer`, `choose_address`, `approve`,
`reject` or `change_constraints` and calls `run_until_pause` again.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from moodmeals.core.actions import (
    AskAction,
    ProposeAction,
    ProtocolError,
    ToolCallAction,
    check_tool_params,
    parse_action,
)
from moodmeals.core.events import Event
from moodmeals.core.guard import Guard, StopReason
from moodmeals.core.redaction import redact_text
from moodmeals.core.state import RunState
from moodmeals.core.validator import DRY_RUN_DISCLAIMER, Constraints, validate_plan
from moodmeals.core.view import (
    build_model_view,
    summarise_menu_items,
    summarise_products,
    summarise_restaurants,
)
from moodmeals.models.llm import LLMClient, LLMError
from moodmeals.models.types import Plan, PlanItem
from moodmeals.providers.base import ActionProvider
from moodmeals.providers.gate import WriteAction, WriteGate
from moodmeals.tools.normalise import (
    parse_dish_search,
    parse_menu,
    parse_products,
    parse_restaurants,
)

CART_UPDATED_MESSAGE = (
    "Your Swiggy cart was updated. Open the Swiggy app to see the real bill (it will be higher "
    "than the item total once fees and taxes are added) and to place the order there. "
    "MoodMeals does not place orders or take payment."
)

STOP_MESSAGES: dict[str, str] = {
    "cancelled": "Stopped at your request.",
    "max_iterations": "I ran out of steps before I could verify a plan.",
    "max_tool_calls": "I used all my tool calls before I could verify a plan.",
    "max_seconds": "I ran out of time before I could verify a plan.",
    "protocol_error": "The assistant could not follow the required format, so I stopped.",
    "model_error": "The model could not be reached, so I stopped.",
    "could_not_verify": "I could not produce a verified plan.",
    "no_address": "No saved delivery address was found.",
    "address_error": "I could not read your saved addresses.",
    "write_failed": "The action did not go through. Nothing was retried.",
    "cart_unverified": "I could not check your existing cart, so I changed nothing.",
}


class Agent:
    def __init__(self, llm: LLMClient, provider: ActionProvider, guard: Guard | None = None):
        self.llm = llm
        self.provider = provider
        self.guard = guard or Guard()
        self.gate = WriteGate(provider.mode, self._execute_write)
        self._address_id: str | None = None

    # ------------------------------------------------------------------ #
    # Starting and resuming
    # ------------------------------------------------------------------ #

    def start(self, user_text: str, constraints: Constraints | None = None) -> RunState:
        state = RunState(
            mode=self.provider.mode,
            user_text=redact_text(user_text)[:500],
            constraints=constraints or Constraints(),
        )
        state.add_event("user_input", "user", {"text": user_text[:500]})
        return state

    def provide_answer(self, state: RunState, text: str) -> None:
        if state.waiting != "answer" or not state.pending_question:
            raise ValueError("The run is not waiting for an answer")
        q = state.pending_question
        answer = redact_text(text, state._sensitive)[:200]
        state.answers.append({"q": q["question"], "a": answer})
        _apply_answer(state, q.get("field", "other"), answer)
        state.waiting, state.pending_question = None, None
        state.add_event("user_input", "user", {"answer": answer, "field": q.get("field")})

    def address_options(self, state: RunState) -> list[dict[str, str]]:
        """For the UI picker only: handle, label and text. Never sent to the model."""
        return list(state._address_display)

    def choose_address(self, state: RunState, handle: str) -> None:
        if state.waiting != "address" or handle not in state._address_ids:
            raise ValueError("The run is not waiting for that address choice")
        label = next(a["label"] for a in state._address_display if a["handle"] == handle)
        self._bind_address(state, handle, label)
        state.waiting = None
        state.add_event("user_input", "user", {"address_choice": handle, "label": label})

    def change_constraints(self, state: RunState, **patch: Any) -> None:
        """The person changed their mind mid-run (R9.2): update, do not restart."""
        if state.phase in ("DONE", "STOPPED"):
            raise ValueError("The run has ended")
        state.constraints = state.constraints.model_copy(update=patch)
        state.add_event("user_input", "user", {"changed": sorted(patch)})
        if state.plan is not None:
            state.plan, state.pending_write = None, None
            state.phase = "PROPOSE"
        state.notes.append("The person changed their constraints. Update the plan; do not restart.")

    def reject(self, state: RunState) -> None:
        """'Another idea' (R4.2), or a rejected order step."""
        if state.phase != "AWAITING_APPROVAL" or state.plan is None:
            raise ValueError("There is no plan waiting for approval")
        state.add_event("approval", "user", {"decision": "rejected", "step": state.pending_write})
        if state.pending_write == "order":
            state.outcome = {
                "kind": "order_not_placed",
                "message": "The cart was updated but no order was placed.",
            }
            state.phase, state.pending_write = "DONE", None
            return
        state.rejected_plans.append(_plan_summary(state.plan))
        state.plan, state.pending_write, state.phase = None, None, "PROPOSE"
        state.notes.append("The person rejected that plan. Propose a different option.")

    def cancel(self, state: RunState) -> None:
        """Stop now, wherever the run is paused. Nothing is written (R9)."""
        if state.phase in ("DONE", "STOPPED"):
            return
        state.cancelled = True
        if state.plan:
            state.add_event("approval", "user", {"decision": "cancelled"})
        self._stop(state, "cancelled")

    def approve(self, state: RunState) -> None:
        """Approve the one write shown on screen. The cart step and the order step are separate."""
        if state.phase != "AWAITING_APPROVAL" or state.plan is None or not state.pending_write:
            raise ValueError("There is nothing waiting for approval")
        action = _write_action(state)
        self._address_id = state._address_ids.get(state.address_handle or "")
        if state.mode == "live" and not self._cart_guard(state, action):
            return  # blocked or stopped: no approval is issued and nothing is written
        approval = self.gate.issue_approval(action)
        state.add_event(
            "approval",
            "user",
            {"decision": "approved", "tool": action.tool, "hash": action.hash[:12]},
        )
        self._address_id = state._address_ids.get(state.address_handle or "")
        outcome = self.gate.execute(action, approval)
        kind = "write_executed" if outcome.status in ("executed", "simulated") else "write_blocked"
        state.add_event(
            kind, "code", {"tool": action.tool, "status": outcome.status, "reason": outcome.reason}
        )
        state.phase = "AWAITING_APPROVAL"
        if outcome.status == "dry_run_preview":
            state.outcome = {
                "kind": "dry_run_preview",
                "message": "Dry-run: nothing was changed. This is what would happen.",
                "would_do": outcome.result,
            }
            state.phase, state.pending_write = "DONE", None
        elif outcome.status in ("executed", "simulated"):
            if state.pending_write == "cart" and state.mode == "live":
                # Live mode only updates the cart: no order, no payment tool (R10.7).
                state.outcome = {"kind": "cart_updated", "message": CART_UPDATED_MESSAGE}
                state.phase, state.pending_write = "DONE", None
            elif state.pending_write == "cart":
                state.pending_write = "order"  # a separate approval for the order (R10.3)
            else:
                state.outcome = {"kind": "order_placed", "simulated": outcome.status == "simulated"}
                state.phase, state.pending_write = "DONE", None
        else:
            self._stop(state, "write_failed")

    # ------------------------------------------------------------------ #
    # The loop
    # ------------------------------------------------------------------ #

    def run_until_pause(self, state: RunState) -> Iterator[Event]:
        seen = len(state.events)
        while not _paused(state):
            if state.address_handle is None:
                self._resolve_address(state)
            else:
                reason = self.guard.check(state)
                if reason:
                    self._stop(state, reason)
                else:
                    state.iterations += 1
                    self._step(state)
            yield from state.events[seen:]
            seen = len(state.events)

    def run(self, state: RunState) -> RunState:
        for _ in self.run_until_pause(state):
            pass
        return state

    # ------------------------------------------------------------------ #
    # Steps
    # ------------------------------------------------------------------ #

    def _resolve_address(self, state: RunState) -> None:
        """Code lists addresses. One: use it and say so. Several: the person picks (R3)."""
        result = self.provider.call("list_addresses", {})
        if not result.ok or result.data is None:
            self._stop(state, "address_error")
            return
        addresses = _parse_addresses(result.data)
        if not addresses:
            self._stop(state, "no_address")
            return
        state.register_sensitive(*(a["text"] for a in addresses))
        state._address_ids = {f"address_{i}": a["id"] for i, a in enumerate(addresses, 1)}
        state._address_display = [
            {"handle": f"address_{i}", "label": a["label"], "text": a["text"]}
            for i, a in enumerate(addresses, 1)
        ]
        if len(addresses) == 1:
            self._bind_address(state, "address_1", addresses[0]["label"])
            state.add_event(
                "tool_result_summary",
                "code",
                {"note": f"Using your saved {addresses[0]['label']} address"},
            )
            return
        if not self.guard.record_question(state):
            self._stop(
                state, "address_error", "There was no question left to ask which address to use."
            )
            return
        state.waiting = "address"
        state.add_event(
            "question",
            "code",
            {"kind": "address_picker", "options": [a["label"] for a in addresses]},
        )

    def _bind_address(self, state: RunState, handle: str, label: str) -> None:
        state.address_handle, state.address_label = handle, label
        self._address_id = state._address_ids[handle]

    def _step(self, state: RunState) -> None:
        view = build_model_view(state, self.guard.budget)
        state.notes.clear()
        try:
            action = parse_action(self.llm.next_action(view))
        except ProtocolError as e:
            self._protocol_error(state, str(e))
            return
        except LLMError as e:
            detail = str(e)[:200]  # adapters never put keys or request bodies in this text
            state.add_event("error", "code", {"kind": "model_error", "detail": detail})
            self._stop(state, "model_error", f"{STOP_MESSAGES['model_error']} ({detail})")
            return
        if isinstance(action, ToolCallAction):
            self._tool_call(state, action)
        elif isinstance(action, AskAction):
            self._ask(state, action)
        else:
            self._propose(state, action)

    def _protocol_error(self, state: RunState, detail: str) -> None:
        state.protocol_errors += 1
        state.add_event("error", "model", {"kind": "protocol", "detail": detail})
        if state.protocol_errors >= 2:
            self._stop(state, "protocol_error")
        else:
            state.notes.append(
                f"Your last reply was not accepted: {detail}. Reply with exactly one action."
            )

    def _tool_call(self, state: RunState, a: ToolCallAction) -> None:
        problem = check_tool_params(a.name, a.params, state.ledger)
        if problem:
            self._protocol_error(state, f"{a.name}: {problem}")
            return
        state.protocol_errors = 0
        if state.phase == "CHECKIN":
            state.phase = "GATHER"
        params = {**a.params, "address_id": self._address_id}
        for _attempt in (1, 2):  # retry a failed read once (R9.1)
            state.tool_calls += 1
            state.add_event(
                "tool_call", "model", {"tool": a.name, "params": a.params}, rationale=a.rationale
            )
            result = self.provider.call(a.name, params)
            if (
                result.ok
                or result.error is None
                or result.error.kind in ("bad_params", "not_found")
            ):
                break
        if not result.ok or result.data is None:
            kind = result.error.kind if result.error else "error"
            state.tool_log.append({"tool": a.name, "params": a.params, "error": kind})
            state.add_event("tool_result_summary", "code", {"tool": a.name, "error": kind})
            state.notes.append(f"{a.name} failed ({kind}). Try another query, restaurant or path.")
            return
        summary = self._ingest(state, a.name, a.params, result.data)
        if summary.get("total") == 0:
            summary["note"] = "nothing found"
        state.tool_log.append({"tool": a.name, "params": a.params, "result": summary})
        state.add_event(
            "tool_result_summary", "code", {"tool": a.name, "count": summary.get("total", 0)}
        )

    def _ingest(
        self, state: RunState, name: str, params: dict[str, Any], data: dict[str, Any]
    ) -> dict[str, Any]:
        ledger = state.ledger
        if name == "search_restaurants":
            rs = parse_restaurants(data)
            ledger.add_restaurants(rs)
            return {**summarise_restaurants(rs), "has_more": bool(data.get("hasMore"))}
        if name == "get_menu":
            restaurant, items = parse_menu(data)
            if restaurant:
                known = ledger.restaurants.get(restaurant.id)
                if known:  # the menu header does not carry the "(Ad)" marker; keep what search saw
                    restaurant = restaurant.model_copy(update={"sponsored": known.sponsored})
                ledger.add_restaurants([restaurant])
            ledger.add_menu_items(items)
            return {
                **summarise_menu_items(items, state),
                "restaurant_id": params.get("restaurant_id"),
                "open": restaurant.open if restaurant else None,
                "has_more_pages": bool(data.get("hasMore")),
            }
        if name == "search_dish":
            items = parse_dish_search(data, str(params["restaurant_id"]))
            ledger.add_menu_items(items)
            return {**summarise_menu_items(items, state), "restaurant_id": params["restaurant_id"]}
        ps = parse_products(data)
        ledger.add_products(ps)
        return {**summarise_products(ps, state), "has_more": "nextOffset" in data}

    def _ask(self, state: RunState, a: AskAction) -> None:
        state.protocol_errors = 0
        if not self.guard.record_question(state):
            state.add_event("question", "model", {"refused": True}, rationale=a.rationale)
            state.notes.append(
                "No questions are left. Decide now and list your assumptions in the plan."
            )
            return
        state.pending_question = {"question": a.question, "field": a.field, "options": a.options}
        state.waiting = "answer"
        state.add_event(
            "question",
            "model",
            {"question": a.question, "field": a.field, "options": a.options},
            rationale=a.rationale,
        )

    def _propose(self, state: RunState, a: ProposeAction) -> None:
        state.protocol_errors = 0
        state.phase = "VALIDATE"
        plan = build_plan(a, state)
        exhausted = state.questions_asked >= self.guard.budget.max_questions
        result = validate_plan(
            plan,
            state.ledger,
            state.constraints,
            mode=state.mode,
            required_assumptions=state.missing_signals if exhausted else [],
        )
        if not result.ok:
            state.validation_errors = [
                {"check": i.check, "code": i.code, "message": i.message, "entity": i.entity}
                for i in result.errors
            ]
            state.add_event(
                "validation",
                "code",
                {"ok": False, "errors": [i.code for i in result.errors]},
                rationale=a.rationale,
            )
            if self.guard.record_validation_retry(state):
                state.phase = "PROPOSE"
                state.notes.append(
                    "That plan did not pass verification. Fix the listed errors and propose again."
                )
            else:
                self._stop(state, "could_not_verify")
            return
        state.plan, state.validation_errors = plan, []
        state.warnings = [
            {"check": i.check, "code": i.code, "message": i.message, "entity": i.entity}
            for i in result.warnings
        ]
        state.phase, state.pending_write = "AWAITING_APPROVAL", "cart"
        state.replace_confirmed = False
        state.cart_check = None
        if state.mode == "live":
            self._check_cart(state)
        state.add_event(
            "validation", "code", {"ok": True, "warnings": [i.code for i in result.warnings]}
        )
        state.add_event("plan", "model", plan.model_dump(), rationale=a.rationale)

    # ------------------------------------------------------------------ #

    def _stop(self, state: RunState, reason: StopReason | str, message: str | None = None) -> None:
        state.phase, state.stop_reason, state.waiting = "STOPPED", reason, None
        state.outcome = {
            "kind": "stopped",
            "reason": reason,
            "message": message or STOP_MESSAGES.get(reason, "The run stopped."),
            "errors": [e["code"] for e in state.validation_errors],
            "best_plan": state.plan.model_dump() if state.plan else None,
        }
        state.add_event("stop", "code", {"reason": reason})

    def confirm_replace(self, state: RunState) -> None:
        """The person agrees to change a cart that already has items (DQ6). Does not write."""
        if (
            state.mode != "live"
            or state.phase != "AWAITING_APPROVAL"
            or state.pending_write != "cart"
            or state.cart_check != "not_empty"
        ):
            raise ValueError("There is no existing cart waiting for confirmation")
        state.replace_confirmed = True
        state.add_event("approval", "user", {"decision": "replace_confirmed", "step": "cart"})

    def _check_cart(self, state: RunState) -> None:
        """Read whether the cart this plan will change is empty. Status only (DQ6)."""
        assert state.plan is not None
        food = state.plan.path == "order_in"
        params: dict[str, Any] = {"cart": "food" if food else "im"}
        if food:
            params["address_id"] = self._address_id
        result = self.provider.call("get_cart_state", params)
        empty = result.data.get("empty") if result.ok and result.data else None
        state.cart_check = "unknown" if not isinstance(empty, bool) else (
            "empty" if empty else "not_empty"
        )  # fmt: skip
        words = {"empty": "is empty", "not_empty": "already has items", "unknown": "was not read"}
        state.add_event(
            "tool_result_summary",
            "code",
            {"note": f"Your {'Food' if food else 'Instamart'} cart {words[state.cart_check]}"},
        )

    def _cart_guard(self, state: RunState, action: WriteAction) -> bool:
        """Live cart updates only: check the cart again just before writing. An unreadable cart
        stops the run; a cart with items needs the person's explicit confirmation."""
        if state.pending_write != "cart":
            return True
        self._check_cart(state)
        if state.cart_check == "unknown":
            self._stop(state, "cart_unverified")
            return False
        if state.cart_check == "not_empty" and not state.replace_confirmed:
            state.add_event(
                "write_blocked",
                "code",
                {"tool": action.tool, "status": "blocked", "reason": "cart_not_empty"},
            )
            return False
        return True

    def _execute_write(self, tool: str, params: dict[str, Any]) -> Any:
        result = self.provider.call(tool, {**params, "address_id": self._address_id})
        if not result.ok:
            raise RuntimeError(result.error.kind if result.error else "error")
        return result.data


# ---------------------------------------------------------------------- #
# Helpers
# ---------------------------------------------------------------------- #


def _paused(state: RunState) -> bool:
    return state.phase in ("DONE", "STOPPED", "AWAITING_APPROVAL") or state.waiting is not None


def _parse_addresses(data: dict[str, Any]) -> list[dict[str, str]]:
    """Real Swiggy keys (seen 2026-10-05): id, addressLine, addressCategory, addressTag.
    The short keys are the mock world's."""
    out = []
    for a in data.get("addresses") or []:
        aid = a.get("id") or a.get("addressId")
        if aid is None:
            continue
        label = a.get("addressCategory") or a.get("category") or a.get("addressTag") or a.get("tag")
        out.append(
            {
                "id": str(aid),
                "label": str(label or "Saved address"),
                "text": str(a.get("addressLine") or a.get("address") or ""),
            }
        )
    return out


def _apply_answer(state: RunState, field: str, text: str) -> None:
    """Hard constraints come from answers by code, not by model guess (design 9.1)."""
    low = text.lower()
    if field == "diet":
        veg = ("veg" in low or "jain" in low) and "non" not in low
        state.constraints = state.constraints.model_copy(
            update={"veg": veg or state.constraints.veg}
        )
    elif field == "budget":
        m = re.search(r"\d[\d,]*", text)
        if m:
            state.constraints = state.constraints.model_copy(
                update={"budget": int(m.group(0).replace(",", ""))}
            )
    elif field == "party":
        m = re.search(r"\d+", text)
        if m and int(m.group(0)) >= 1:
            state.constraints = state.constraints.model_copy(update={"party_size": int(m.group(0))})


def _plan_summary(plan: Plan) -> dict[str, Any]:
    return {"path": plan.path, "items": [i.name for i in plan.items], "item_total": plan.item_total}


def build_plan(a: ProposeAction, state: RunState) -> Plan:
    """Names, prices, total, ETA and the dry-run note come from the ledger, not the model (R7.7)."""
    ledger = state.ledger
    items: list[PlanItem] = []
    restaurant_ids: set[str] = set()
    for it in a.items:
        if a.path == "order_in":
            m = ledger.menu_items.get(it["id"])
            if m:
                restaurant_ids.add(m.restaurant_id)
            items.append(
                PlanItem(
                    kind="dish", entity_id=it["id"], name=m.name if m else "unknown item",
                    qty=it["qty"], unit_price=m.price if m else 0,
                )
            )  # fmt: skip
        else:
            p = ledger.products.get(it["id"])
            v = (
                next((v for v in p.variants if v.spin_id == it.get("variant_id")), None)
                if p
                else None
            )
            items.append(
                PlanItem(
                    kind="product", entity_id=it["id"], name=p.name if p else "unknown item",
                    variant_id=it.get("variant_id"), qty=it["qty"], unit_price=v.price if v else 0,
                )
            )  # fmt: skip
    rid = a.restaurant_id or (next(iter(restaurant_ids)) if len(restaurant_ids) == 1 else None)
    restaurant = ledger.restaurants.get(rid) if rid else None
    return Plan(
        path=a.path,
        reason=a.reason,
        items=items,
        item_total=sum(i.qty * i.unit_price for i in items),
        restaurant_id=rid if a.path == "order_in" else None,
        eta_minutes=restaurant.eta_minutes if restaurant and a.path == "order_in" else None,
        assumptions=a.assumptions,
        mode_notes=[DRY_RUN_DISCLAIMER] if state.mode == "dry_run" else [],
    )


def _write_action(state: RunState) -> WriteAction:
    plan = state.plan
    assert plan is not None
    if state.pending_write == "order":
        return WriteAction("place_food_order" if plan.path == "order_in" else "checkout", {})
    if plan.path == "order_in":
        return WriteAction(
            "update_food_cart",
            {
                "restaurant_id": plan.restaurant_id,
                "items": [{"id": i.entity_id, "qty": i.qty} for i in plan.items],
            },
        )
    return WriteAction(
        "update_cart",
        {
            "items": [
                {"product_id": i.entity_id, "spin_id": i.variant_id, "qty": i.qty}
                for i in plan.items
            ]
        },
    )
