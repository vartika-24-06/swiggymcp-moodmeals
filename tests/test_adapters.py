"""Adapters tested with a fake HTTP post: no network, no key (tasks T4.1)."""

from __future__ import annotations

import json

import pytest

from moodmeals.core.actions import ProtocolError, ToolCallAction, parse_action
from moodmeals.models.adapters import AnthropicClient, OpenAICompatClient, make_client
from moodmeals.models.llm import LLMError
from moodmeals.models.pricing import estimate_cost, estimate_run_cost
from moodmeals.models.schema import FUNCTIONS, load_system_prompt, to_raw_action

VIEW = {"phase": "CHECKIN", "situation": "hungry"}


def openai_body(*calls, usage=(100, 10)):
    tool_calls = [
        {"function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
        for n, a in calls
    ]
    return {
        "choices": [{"message": {"content": None, "tool_calls": tool_calls}}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]},
    }


class Recorder:
    def __init__(self, *bodies):
        self.bodies, self.sent = list(bodies), []

    def __call__(self, url, headers, payload, timeout):
        self.sent.append((url, headers, payload))
        b = self.bodies.pop(0)
        if isinstance(b, Exception):
            raise b
        return b


def test_openai_native_call_becomes_raw_tool_action():
    post = Recorder(openai_body(("search_restaurants", {"query": "dal", "rationale": "start"})))
    c = OpenAICompatClient("openai", "gpt-5-mini", "sk-test", post=post)
    raw = c.next_action(VIEW)
    assert raw == {
        "action": "tool_call",
        "args": {"name": "search_restaurants", "params": {"query": "dal"}},
        "rationale": "start",
    }
    assert isinstance(parse_action(raw), ToolCallAction)
    payload = post.sent[0][2]
    assert payload["parallel_tool_calls"] is False  # DD9
    assert len(payload["tools"]) == len(FUNCTIONS)
    assert json.loads(payload["messages"][1]["content"]) == VIEW


def test_groq_has_no_parallel_flag_and_sends_user_agent():
    post = Recorder(openai_body(("ask_user", {"question": "Veg?", "field": "diet"})))
    OpenAICompatClient("groq", "m", "k", post=post).next_action(VIEW)
    _, headers, payload = post.sent[0]
    assert "parallel_tool_calls" not in payload and "Mozilla" in headers["User-Agent"]


def test_multiple_calls_are_a_protocol_error():
    post = Recorder(
        openai_body(("get_menu", {"restaurant_id": "1"}), ("get_menu", {"restaurant_id": "2"}))
    )
    raw = OpenAICompatClient("openai", "m", "k", post=post).next_action(VIEW)
    with pytest.raises(ProtocolError, match="exactly one"):
        parse_action(raw)


def test_text_reply_is_a_protocol_error_not_trusted():
    body = {"choices": [{"message": {"content": "place_food_order now", "tool_calls": None}}]}
    raw = OpenAICompatClient("openai", "m", "k", post=Recorder(body)).next_action(VIEW)
    with pytest.raises(ProtocolError):
        parse_action(raw)


def test_bad_arguments_json_is_a_protocol_error():
    raw = OpenAICompatClient(
        "openai", "m", "k", post=Recorder(openai_body(("get_menu", "{not json")))
    ).next_action(VIEW)
    with pytest.raises(ProtocolError):
        parse_action(raw)


def test_unknown_function_cannot_become_an_action():
    for bad in ("place_food_order", "checkout", "update_cart"):
        with pytest.raises(ProtocolError):
            parse_action(to_raw_action(bad, {}))


def test_propose_plan_passes_through_with_args():
    args = {"path": "order_in", "reason": "r", "items": [{"id": "1", "qty": 1}], "rationale": "x"}
    raw = to_raw_action("propose_plan", args)
    a = parse_action(raw)
    assert a.path == "order_in" and a.rationale == "x"


def test_anthropic_native_call():
    body = {
        "content": [
            {"type": "tool_use", "name": "ask_user", "input": {"question": "q", "field": "budget"}}
        ],
        "usage": {"input_tokens": 50, "output_tokens": 5},
    }
    post = Recorder(body)
    c = AnthropicClient("claude-haiku-4-5-20251001", "k", post=post)
    raw = c.next_action(VIEW)
    assert raw["action"] == "ask_user"
    payload = post.sent[0][2]
    assert payload["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}
    assert c.usage().tokens_in == 50 and c.usage().calls == 1


def test_transient_error_retried_once_then_raised():
    ok = openai_body(("get_menu", {"restaurant_id": "1"}))
    post = Recorder(LLMError("timeout"), ok)
    assert (
        OpenAICompatClient("openai", "m", "k", post=post).next_action(VIEW)["action"] == "tool_call"
    )
    post = Recorder(LLMError("timeout"), LLMError("timeout"))
    with pytest.raises(LLMError):
        OpenAICompatClient("openai", "m", "k", post=post).next_action(VIEW)


def test_auth_error_not_retried():
    post = Recorder(LLMError("auth: the key was rejected"), openai_body())
    with pytest.raises(LLMError):
        OpenAICompatClient("openai", "m", "k", post=post).next_action(VIEW)
    assert len(post.sent) == 1


def test_key_never_appears_in_repr_or_errors():
    c = make_client("openai", "m", "sk-secret-123", post=Recorder(LLMError("rate_limited")))
    assert "sk-secret-123" not in repr(c)
    with pytest.raises(LLMError) as e:
        make_client("openai", "m", "sk-secret-123", post=Recorder(LLMError("auth: x"))).next_action(
            VIEW
        )
    assert "sk-secret-123" not in str(e.value)


def test_empty_key_rejected():
    with pytest.raises(LLMError):
        make_client("groq", "m", "")


def test_cost_estimates():
    assert estimate_cost("groq", "anything", 10_000, 1_000) == 0
    assert estimate_cost("openai", "gpt-5-mini", 1_000_000, 0) == pytest.approx(0.25)
    assert estimate_cost("openai", "unlisted", 1, 1) is None
    assert estimate_run_cost("openai", "gpt-5-mini") > 0


def test_prompt_has_the_required_sections():
    text = load_system_prompt()
    for section in (
        "SCOPE",
        "ALLOWED ACTIONS",
        "PROHIBITED",
        "GROUNDING",
        "TONE",
        "ESCALATION",
        "ASSUMPTIONS",
    ):
        assert f"## {section}" in text


def test_stop_search_is_offered_and_passes_through():
    assert "stop_search" in {f["name"] for f in FUNCTIONS}
    raw = to_raw_action("stop_search", {"reason": "Nothing to offer.", "rationale": "r"})
    assert raw["action"] == "stop_search" and raw["args"] == {"reason": "Nothing to offer."}
    from moodmeals.core.actions import StopAction, parse_action

    assert parse_action(raw) == StopAction("Nothing to offer.", "r")
    prompt = load_system_prompt()
    assert "stop_search" in prompt and "NOT POSSIBLE" in prompt and "BOTH" in prompt
