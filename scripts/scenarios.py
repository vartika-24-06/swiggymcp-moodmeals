"""Five manual scenarios on the MOCK world with a real model (tasks T4.3).

    $env:OPENAI_API_KEY = "..."
    python scripts/scenarios.py openai gpt-5-mini

Questions are auto-answered with the canned reply for that scenario. Prints one compact
block per scenario and writes full traces to scripts/_out/ (git-ignored). Paste the
printed summary back; it contains only synthetic data.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from moodmeals.core.loop import Agent
from moodmeals.core.validator import Constraints
from moodmeals.models.adapters import make_client
from moodmeals.models.llm import LLMError
from moodmeals.providers.mock import MockProvider
from moodmeals.providers.switches import Switches

KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# name, user text, canned answers by question field, switches, constraints, addresses
SCENARIOS = [
    ("tired_veg", "Bahut thaka hua hoon, kuch halka chahiye", {"diet": "veg", "budget": "300"},
     Switches(), {}, 1),
    ("wants_to_cook", "Aaj ghar pe kuch simple banana hai, dal chawal jaisa", {"diet": "veg"},
     Switches(), {}, 1),
    ("all_closed", "Biryani khani hai", {"diet": "non veg", "budget": "500"},
     Switches(all_closed=True), {}, 1),
    ("tight_budget", "Dinner for 2, budget 150", {"diet": "veg", "party": "2"},
     Switches(price_scale=1.5), {"budget": 150}, 1),
    ("three_addresses", "Kuch bhi, bas jaldi", {"diet": "veg", "time": "30"},
     Switches(), {}, 3),
]  # fmt: skip


def run_one(llm, name, text, answers, switches, cons, n_addr, outdir: Path) -> dict:
    agent = Agent(llm, MockProvider(seed=1, switches=switches, n_addresses=n_addr))
    state = agent.start(text, Constraints(**cons))
    asked: list[str] = []
    try:
        for _ in range(12):
            agent.run(state)
            if state.waiting == "answer":
                q = state.pending_question or {}
                asked.append(f"{q.get('field')}: {q.get('question')}")
                agent.provide_answer(state, answers.get(q.get("field"), "no preference"))
            elif state.waiting == "address":
                agent.choose_address(state, agent.address_options(state)[0]["handle"])
            else:
                break
    except LLMError as e:
        state.notes.append(f"LLMError {e}")
    (outdir / f"{name}.json").write_text(
        json.dumps([e.model_dump() for e in state.events], indent=1, default=str), encoding="utf-8"
    )
    plan = state.plan
    return {
        "scenario": name,
        "phase": state.phase,
        "stop": state.stop_reason,
        "questions": asked,
        "tool_calls": state.tool_calls,
        "protocol_errors": [e.payload.get("detail") for e in state.events if e.type == "error"],
        "plan": (
            None
            if not plan
            else {
                "path": plan.path,
                "items": [f"{i.qty} x {i.name} @ {i.unit_price}" for i in plan.items],
                "total": plan.item_total,
                "reason": plan.reason,
                "assumptions": plan.assumptions,
            }
        ),
        "validation_failures": state.validation_retries,
    }


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in KEY_ENV:
        print("usage: python scripts/scenarios.py <provider> <model>")
        return 2
    provider, model = sys.argv[1:]
    key = os.environ.get(KEY_ENV[provider], "")
    if not key:
        print(f"Set {KEY_ENV[provider]} first.")
        return 2
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    outdir = Path(__file__).parent / "_out"
    outdir.mkdir(exist_ok=True)
    total_cost = 0.0
    for name, text, answers, sw, cons, n in SCENARIOS:
        llm = make_client(provider, model, key)
        print(json.dumps(run_one(llm, name, text, answers, sw, cons, n, outdir), indent=1))
        total_cost += llm.cost_estimate() or 0
    print(f"\nest. total cost: ${total_cost:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
