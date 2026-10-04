"""Spike B (tasks.md T0.3, design DQ3): can a model drive our one-action-per-turn loop?

For each model it sends 5 canned prompts, N times each, with 3 fake read-only
tools plus the two non-tool actions (ask_user, propose_plan), and scores:

  valid   exactly one action, a known name, arguments that match the schema
  right   the valid action is also the sensible one for that prompt

Two modes per model:
  native  the provider's own function calling
  json    no function calling; the model is told to reply with one JSON object

Everything sent is synthetic. No Swiggy data, no personal data. API keys are read
from environment variables, kept in memory and never printed or written.

Run:  python spikes/tool_calling.py --run gemini:<model> --run groq:<model> ...
See spikes/README.md for the full steps.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# The action contract: 3 fake tools + 2 non-tool actions, as JSON-schema.
# --------------------------------------------------------------------------- #

ACTIONS: list[dict[str, Any]] = [
    {
        "name": "search_restaurants",
        "description": "Search restaurants available for delivery. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Dish or cuisine to look for"},
                "veg_only": {"type": "boolean", "description": "True for vegetarian only"},
            },
            "required": ["query", "veg_only"],
        },
    },
    {
        "name": "search_products",
        "description": "Search grocery products for cooking at home. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Ingredient or product to look for"},
                "max_results": {"type": "integer", "description": "How many results, 1 to 10"},
            },
            "required": ["query", "max_results"],
        },
    },
    {
        "name": "get_delivery_estimate",
        "description": "Get the delivery time in minutes for one restaurant. Read-only.",
        "parameters": {
            "type": "object",
            "properties": {"restaurant_id": {"type": "string"}},
            "required": ["restaurant_id"],
        },
    },
    {
        "name": "ask_user",
        "description": "Ask the person ONE short question when a key detail is missing.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "propose_plan",
        "description": "Propose the final plan once you have enough information.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "enum": ["cook", "order_in"]},
                "summary": {"type": "string", "description": "One or two sentences"},
                "items": {
                    "type": "array",
                    "description": "Chosen restaurant dishes or products",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "qty": {"type": "integer"},
                        },
                        "required": ["id", "qty"],
                    },
                },
            },
            "required": ["path", "summary", "items"],
        },
    },
]
ACTION_BY_NAME = {a["name"]: a for a in ACTIONS}

SYSTEM = (
    "You are the decision step of a food-decision agent that helps a person choose "
    "between cooking at home and ordering in. On every turn you must do exactly one "
    "thing, by calling exactly one of the provided functions. Never reply with plain "
    "text. Use ask_user only when a key detail is missing. Use propose_plan only when "
    "tool results already give you what you need, and only use ids that appear in them."
)

JSON_SYSTEM = (
    SYSTEM.replace(
        "by calling exactly one of the provided functions", "by replying with one JSON object"
    )
    + ' Reply with ONLY a JSON object of the form {"action": "<name>", "args": {...}}. '
    "The allowed actions and their args schemas are:\n"
    + "\n".join(f"- {a['name']}: {json.dumps(a['parameters'])}" for a in ACTIONS)
)

RESULTS_P5 = (
    'Tool results so far from search_restaurants(query="khichdi", veg_only=true):\n'
    '[{"id": "r_101", "name": "Sample Kitchen", "eta_min": 25}, '
    '{"id": "r_102", "name": "Demo Dhaba", "eta_min": 40}]'
)


@dataclass
class Prompt:
    pid: str
    text: str
    expect_action: str
    expect_args: Callable[[dict[str, Any]], bool] = lambda _a: True


PROMPTS = [
    Prompt(
        "P1_order_veg",
        "I'm vegetarian, I have 30 minutes, and I want something hot. "
        "I'm ordering in. Find options.",
        "search_restaurants",
        lambda a: a.get("veg_only") is True,
    ),
    Prompt(
        "P2_cook_named",
        "I want to cook paneer butter masala tonight. Find me the ingredients.",
        "search_products",
    ),
    Prompt("P3_vague", "Hungry. No idea what I want.", "ask_user"),
    Prompt(
        "P4_hinglish",
        "yaar aaj kuch halka khana hai, ghar pe banana hai, khichdi jaisa. ingredients dhundo",
        "search_products",
    ),
    Prompt(
        "P5_ground_plan",
        "Ordering in, vegetarian, something light.\n" + RESULTS_P5 + "\nDecide the next step.",
        "propose_plan",
        lambda a: (
            a.get("path") == "order_in"
            and bool(a.get("items"))
            and all(i.get("id") in {"r_101", "r_102"} for i in a["items"])
        ),
    ),
]

# --------------------------------------------------------------------------- #
# Validation (pure functions, no network)
# --------------------------------------------------------------------------- #

_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
}


def schema_errors(value: Any, schema: dict[str, Any], path: str = "args") -> list[str]:
    errs: list[str] = []
    typ = schema.get("type")
    if typ and not _TYPES[typ](value):
        return [f"{path}: expected {typ}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errs.append(f"{path}: {value!r} not in enum")
    if typ == "object":
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            if req not in value:
                errs.append(f"{path}.{req}: missing")
        for key, sub in value.items():
            if key not in props:
                errs.append(f"{path}.{key}: unexpected key")
            else:
                errs.extend(schema_errors(sub, props[key], f"{path}.{key}"))
    if typ == "array" and "items" in schema:
        for i, item in enumerate(value):
            errs.extend(schema_errors(item, schema["items"], f"{path}[{i}]"))
    return errs


@dataclass
class Outcome:
    status: str  # ok | http_error | timeout | parse_error
    name: str | None = None
    args: dict[str, Any] | None = None
    n_actions: int = 0
    note: str = ""
    latency_s: float = 0.0
    retries: int = 0


def judge(outcome: Outcome, prompt: Prompt) -> tuple[bool, bool, str]:
    """Return (valid, right, reason). Reason is a short code, never model text."""
    if outcome.status != "ok":
        return False, False, outcome.status
    if outcome.n_actions != 1:
        return False, False, f"n_actions={outcome.n_actions}"
    if outcome.name not in ACTION_BY_NAME:
        return False, False, "unknown_action"
    errs = schema_errors(outcome.args, ACTION_BY_NAME[outcome.name]["parameters"])
    if errs:
        return False, False, "schema:" + errs[0][:60]
    right = outcome.name == prompt.expect_action and prompt.expect_args(outcome.args or {})
    return True, right, "ok" if right else "wrong_action_or_args"


# --------------------------------------------------------------------------- #
# Parsing each provider's response (pure functions, no network)
# --------------------------------------------------------------------------- #


def parse_openai_native(body: dict[str, Any]) -> Outcome:
    msg = body["choices"][0]["message"]
    calls = msg.get("tool_calls") or []
    if not calls:
        return Outcome("ok", n_actions=0, note="no_tool_call")
    fn = calls[0]["function"]
    try:
        args = json.loads(fn.get("arguments") or "{}")
    except json.JSONDecodeError:
        return Outcome("parse_error", n_actions=len(calls), note="bad_arguments_json")
    return Outcome("ok", fn.get("name"), args, len(calls))


def parse_anthropic_native(body: dict[str, Any]) -> Outcome:
    calls = [b for b in body.get("content", []) if b.get("type") == "tool_use"]
    if not calls:
        return Outcome("ok", n_actions=0, note="no_tool_call")
    return Outcome("ok", calls[0].get("name"), calls[0].get("input"), len(calls))


def parse_gemini_native(body: dict[str, Any]) -> Outcome:
    cands = body.get("candidates") or []
    if not cands:
        return Outcome("ok", n_actions=0, note="no_candidates")
    parts = (cands[0].get("content") or {}).get("parts") or []
    calls = [p["functionCall"] for p in parts if "functionCall" in p]
    if not calls:
        return Outcome("ok", n_actions=0, note="no_tool_call")
    return Outcome("ok", calls[0].get("name"), calls[0].get("args") or {}, len(calls))


def text_of(provider: str, body: dict[str, Any]) -> str:
    if provider == "anthropic":
        return "".join(
            b.get("text", "") for b in body.get("content", []) if b.get("type") == "text"
        )
    if provider == "gemini":
        cands = body.get("candidates") or [{}]
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts)
    return (body["choices"][0]["message"].get("content")) or ""


def parse_json_text(text: str) -> Outcome:
    """JSON-fallback mode: find the first JSON object in the reply."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start < 0:
        return Outcome("ok", n_actions=0, note="no_json")
    try:
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    except json.JSONDecodeError:
        return Outcome("parse_error", n_actions=1, note="bad_json")
    if not isinstance(obj, dict) or "action" not in obj:
        return Outcome("ok", n_actions=1, name=None, args={}, note="no_action_key")
    args = obj.get("args", {})
    return Outcome("ok", obj["action"], args if isinstance(args, dict) else {"_": args}, 1)


# --------------------------------------------------------------------------- #
# Providers (the only code that touches the network)
# --------------------------------------------------------------------------- #

OPENAI_COMPAT = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


class CallError(Exception):
    def __init__(self, kind: str, note: str = "") -> None:
        super().__init__(kind)
        self.kind = kind
        self.note = note


def _post(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise CallError("http_error", f"HTTP {e.code}") from None
    except TimeoutError:
        raise CallError("timeout") from None
    except urllib.error.URLError as e:
        raise CallError("http_error", f"network: {type(e.reason).__name__}") from None


def call_model(
    provider: str, model: str, mode: str, user_text: str, key: str, timeout: float
) -> Outcome:
    native = mode == "native"
    system = SYSTEM if native else JSON_SYSTEM
    if provider in OPENAI_COMPAT:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
        }
        if native:
            payload["tools"] = [{"type": "function", "function": a} for a in ACTIONS]
            payload["tool_choice"] = "auto"
        body = _post(OPENAI_COMPAT[provider], {"Authorization": f"Bearer {key}"}, payload, timeout)
        return parse_openai_native(body) if native else parse_json_text(text_of(provider, body))
    if provider == "anthropic":
        payload = {
            "model": model,
            "max_tokens": 1024,
            "system": system,
            "messages": [{"role": "user", "content": user_text}],
        }
        if native:
            payload["tools"] = [
                {
                    "name": a["name"],
                    "description": a["description"],
                    "input_schema": a["parameters"],
                }
                for a in ACTIONS
            ]
            payload["tool_choice"] = {"type": "auto"}
        body = _post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
            payload,
            timeout,
        )
        return parse_anthropic_native(body) if native else parse_json_text(text_of(provider, body))
    if provider == "gemini":
        payload = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        }
        if native:
            payload["tools"] = [{"functionDeclarations": ACTIONS}]
            payload["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        body = _post(url, {"x-goog-api-key": key}, payload, timeout)
        return parse_gemini_native(body) if native else parse_json_text(text_of(provider, body))
    raise SystemExit(f"Unknown provider: {provider}")


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #


@dataclass
class Tally:
    provider: str
    model: str
    mode: str
    calls: int = 0
    answered: int = 0
    valid: int = 0
    right: int = 0
    errors: dict[str, int] = field(default_factory=dict)
    reasons: dict[str, int] = field(default_factory=dict)
    latencies: list[float] = field(default_factory=list)
    per_prompt: dict[str, list[int]] = field(default_factory=dict)  # pid -> [valid, right, n]


def one_call(
    provider: str, model: str, mode: str, text: str, key: str, timeout: float, delay: float
) -> Outcome:
    retries = 0
    while True:
        t0 = time.monotonic()
        try:
            out = call_model(provider, model, mode, text, key, timeout)
        except CallError as e:
            if e.note == "HTTP 429" and retries < 2:
                retries += 1
                time.sleep(20 * retries)
                continue
            out = Outcome(e.kind, note=e.note)
        out.latency_s = time.monotonic() - t0
        out.retries = retries
        time.sleep(delay)
        return out


def run_one(spec: str, mode: str, repeats: int, timeout: float, delay: float) -> Tally:
    provider, _, model = spec.partition(":")
    if provider not in KEY_ENV or not model:
        raise SystemExit(
            f"Bad --run value {spec!r}. Use provider:model, provider one of {sorted(KEY_ENV)}"
        )
    key = os.environ.get(KEY_ENV[provider], "")
    if not key:
        raise SystemExit(f"Set {KEY_ENV[provider]} in this terminal first (see spikes/README.md).")
    t = Tally(provider, model, mode)
    for prompt in PROMPTS:
        for _ in range(repeats):
            out = one_call(provider, model, mode, prompt.text, key, timeout, delay)
            valid, right, reason = judge(out, prompt)
            t.calls += 1
            row = t.per_prompt.setdefault(prompt.pid, [0, 0, 0])
            row[2] += 1
            if out.status in ("http_error", "timeout"):
                k = out.note or out.status
                t.errors[k] = t.errors.get(k, 0) + 1
                continue
            t.answered += 1
            t.latencies.append(out.latency_s)
            row[0] += valid
            row[1] += right
            t.valid += valid
            t.right += right
            if reason != "ok":
                t.reasons[reason] = t.reasons.get(reason, 0) + 1
    return t


def pct(n: int, d: int) -> str:
    return f"{n}/{d}" if d else "-"


def report(tallies: list[Tally]) -> str:
    lines = [
        f"{'model':44} {'mode':7} {'valid':>7} {'right':>7} {'errors':>7} {'median s':>8}",
    ]
    for t in tallies:
        med = f"{statistics.median(t.latencies):.1f}" if t.latencies else "-"
        label = f"{t.provider}:{t.model}"[:44]
        lines.append(
            f"{label:44} {t.mode:7} {pct(t.valid, t.answered):>7} {pct(t.right, t.answered):>7} "
            f"{sum(t.errors.values()):>7} {med:>8}"
        )
    lines.append("")
    for t in tallies:
        lines.append(f"{t.provider}:{t.model} [{t.mode}]")
        for pid, (v, r, n) in t.per_prompt.items():
            lines.append(f"  {pid:16} valid {v}/{n}  right {r}/{n}")
        if t.reasons:
            lines.append(f"  failure reasons: {t.reasons}")
        if t.errors:
            lines.append(f"  call errors:     {t.errors}")
    lines.append("")
    lines.append("valid and right are counted over calls that got an answer; errors are separate.")
    return "\n".join(lines)


def selftest() -> None:
    p = PROMPTS[0]
    good = parse_openai_native(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "search_restaurants",
                                    "arguments": '{"query":"khichdi","veg_only":true}',
                                }
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert judge(good, p) == (True, True, "ok")
    bad = parse_openai_native(
        {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "search_restaurants",
                                    "arguments": '{"query":"khichdi","veg_only":"yes"}',
                                }
                            }
                        ]
                    }
                }
            ]
        }
    )
    assert judge(bad, p)[0] is False
    assert (
        parse_json_text('```json\n{"action":"ask_user","args":{"question":"Veg?"}}\n```').name
        == "ask_user"
    )
    gem = parse_gemini_native(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [{"functionCall": {"name": "ask_user", "args": {"question": "?"}}}]
                    }
                }
            ]
        }
    )
    assert judge(gem, PROMPTS[2]) == (True, True, "ok")
    plan = Outcome(
        "ok",
        "propose_plan",
        {"path": "order_in", "summary": "x", "items": [{"id": "r_101", "qty": 1}]},
        1,
    )
    assert judge(plan, PROMPTS[4]) == (True, True, "ok")
    ungrounded = Outcome(
        "ok",
        "propose_plan",
        {"path": "order_in", "summary": "x", "items": [{"id": "zzz", "qty": 1}]},
        1,
    )
    assert judge(ungrounded, PROMPTS[4]) == (True, False, "wrong_action_or_args")
    print("selftest ok")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--run", action="append", default=[], metavar="PROVIDER:MODEL")
    ap.add_argument("--modes", default="native,json", help="comma list of native,json")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument(
        "--delay", type=float, default=2.0, help="seconds between calls (free tiers rate-limit)"
    )
    ap.add_argument("--out", default="spikes/results/tool_calling.json")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return
    if not args.run:
        ap.error("give at least one --run provider:model")
    tallies: list[Tally] = []
    for spec in args.run:
        for mode in args.modes.split(","):
            print(f"running {spec} [{mode}] ...", flush=True)
            tallies.append(run_one(spec, mode.strip(), args.repeats, args.timeout, args.delay))
    text = report(tallies)
    print()
    print(text)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            [
                {
                    "provider": t.provider,
                    "model": t.model,
                    "mode": t.mode,
                    "calls": t.calls,
                    "answered": t.answered,
                    "valid": t.valid,
                    "right": t.right,
                    "errors": t.errors,
                    "reasons": t.reasons,
                    "per_prompt": t.per_prompt,
                    "median_latency_s": statistics.median(t.latencies) if t.latencies else None,
                }
                for t in tallies
            ],
            indent=2,
        )
    )
    print(f"\nSaved counts to {out} (no prompts, replies or keys inside).")


if __name__ == "__main__":
    sys.exit(main())
