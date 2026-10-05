"""Live smoke test: a real model drives the agent on the MOCK world (tasks T4.1, T4.3).

Nothing here touches Swiggy. Run on your own machine (the model APIs are not reachable
from the build sandbox). Keys come from environment variables, never from files:

    $env:GROQ_API_KEY = "..."
    python scripts/smoke_llm.py groq openai/gpt-oss-20b "tired, want something light"

Questions are answered automatically with the first option (or "no preference") so the
run is hands-free; use --ask to answer them yourself.
"""

from __future__ import annotations

import argparse
import os
import sys

from moodmeals.core.loop import Agent
from moodmeals.models.adapters import make_client
from moodmeals.models.llm import LLMError
from moodmeals.providers.mock import MockProvider

KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", choices=sorted(KEY_ENV))
    ap.add_argument("model")
    ap.add_argument("text", nargs="?", default="kya khana hai, batao. Thaka hua hoon.")
    ap.add_argument("--ask", action="store_true", help="answer questions yourself")
    ap.add_argument("--addresses", type=int, default=1)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()

    key = os.environ.get(KEY_ENV[args.provider], "")
    if not key:
        print(f"Set {KEY_ENV[args.provider]} first.")
        return 2
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    llm = make_client(args.provider, args.model, key)
    agent = Agent(llm, MockProvider(seed=args.seed, n_addresses=args.addresses))
    state = agent.start(args.text)
    try:
        while True:
            for e in agent.run_until_pause(state):
                why = f"| {e.rationale}" if e.rationale else ""
                print(f"[{e.type}/{e.actor}] {e.payload} {why}")
            if state.waiting == "answer":
                q = state.pending_question or {}
                opts = q.get("options") or []
                print(f"\nQUESTION: {q.get('question')}  options={opts}")
                reply = input("> ") if args.ask else (opts[0] if opts else "no preference")
                agent.provide_answer(state, reply)
            elif state.waiting == "address":
                opts = agent.address_options(state)
                print("ADDRESS PICK (mock):", [o["label"] for o in opts])
                agent.choose_address(state, opts[0]["handle"])
            else:
                break
    except LLMError as e:
        print("MODEL ERROR:", e)
        return 1

    print("\nphase:", state.phase, "| stop:", state.stop_reason)
    if state.plan:
        p = state.plan
        print(f"PLAN: {p.path} | {p.reason}")
        for it in p.items:
            print(f"  {it.qty} x {it.name} @ {it.unit_price}")
        print("  item total:", p.item_total, "| assumptions:", p.assumptions)
    if state.outcome:
        print("outcome:", state.outcome)
    u = llm.usage()
    cost = llm.cost_estimate()
    print(f"calls={u.calls} tokens in/out={u.tokens_in}/{u.tokens_out} est. cost=",
          "unknown" if cost is None else f"${cost:.4f}")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
