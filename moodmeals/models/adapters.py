"""Vendor adapters for `LLMClient` (tasks T4.1; design 7.4, DD9).

Each turn is stateless: the system prompt plus the current model view as one user message.
The key lives in the client object (memory only) and is sent to the chosen vendor only.
Errors never include the key or the request body.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from moodmeals.models.llm import LLMError
from moodmeals.models.pricing import estimate_cost
from moodmeals.models.schema import FUNCTIONS, load_system_prompt, to_raw_action

OPENAI_COMPAT_URLS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
UA = "Mozilla/5.0 (compatible; moodmeals/0.1)"  # Groq returned 403 without one (Spike B)

Post = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


@dataclass
class Usage:
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0


def httpx_post(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float):
    try:
        r = httpx.post(url, headers=headers, json=payload, timeout=timeout)
    except httpx.TimeoutException:
        raise LLMError("timeout") from None
    except httpx.HTTPError as e:
        raise LLMError(f"network: {type(e).__name__}") from None
    if r.status_code in (401, 403):
        raise LLMError("auth: the key was rejected")
    if r.status_code == 429:
        raise LLMError("rate_limited")
    if r.status_code >= 400:
        raise LLMError(f"http {r.status_code}: {_detail(r)}")
    try:
        return r.json()
    except ValueError:
        raise LLMError("bad response") from None


def _detail(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        return str(err.get("message", "") if isinstance(err, dict) else err)[:160]
    except Exception:
        return ""


class _Base:
    provider = ""

    def __init__(
        self,
        model: str,
        api_key: str,
        *,
        timeout: float = 45.0,
        post: Post = httpx_post,
        system_prompt: str | None = None,
    ):
        if not api_key:
            raise LLMError("auth: no key given")
        self.model = model
        self._key = api_key
        self._timeout = timeout
        self._post = post
        self._system = system_prompt or load_system_prompt()
        self._usage = Usage()

    def __repr__(self) -> str:  # never show the key
        return f"{type(self).__name__}(model={self.model!r})"

    def usage(self) -> Usage:
        return self._usage

    def cost_estimate(self) -> float | None:
        u = self._usage
        return estimate_cost(self.provider, self.model, u.tokens_in, u.tokens_out)

    def _send(self, url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
        """One retry on transient errors (R9.1); a bad key or bad request is not retried."""
        try:
            return self._post(url, headers, payload, self._timeout)
        except LLMError as e:
            if str(e).startswith(("timeout", "network", "rate_limited", "http 5")):
                return self._post(url, headers, payload, self._timeout)
            raise

    @staticmethod
    def _user_text(view: dict[str, Any]) -> str:
        return json.dumps(view, ensure_ascii=False)


class OpenAICompatClient(_Base):
    """OpenAI, Groq and OpenRouter (and Gemini's compatible endpoint if a URL is given)."""

    def __init__(self, provider: str, model: str, api_key: str, *, url: str | None = None, **kw):
        super().__init__(model, api_key, **kw)
        self.provider = provider
        self._url = url or OPENAI_COMPAT_URLS.get(provider, "")
        if not self._url:
            raise LLMError(f"unknown provider: {provider}")

    def next_action(self, view: dict[str, Any]) -> Any:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system},
                {"role": "user", "content": self._user_text(view)},
            ],
            "tools": [{"type": "function", "function": f} for f in FUNCTIONS],
            "tool_choice": "auto",
        }
        if self.provider == "openai":
            payload["parallel_tool_calls"] = False  # DD9: one action per turn
        body = self._send(
            self._url, {"Authorization": f"Bearer {self._key}", "User-Agent": UA}, payload
        )
        self._count(body.get("usage") or {}, "prompt_tokens", "completion_tokens")
        try:
            msg = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("bad response shape") from None
        calls = msg.get("tool_calls") or []
        if not calls:
            return to_raw_action(None, None)
        fn = calls[0].get("function") or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except json.JSONDecodeError:
            return {"error": "Function arguments were not valid JSON"}
        return to_raw_action(fn.get("name"), args, len(calls))

    def _count(self, usage: dict[str, Any], k_in: str, k_out: str) -> None:
        self._usage.calls += 1
        self._usage.tokens_in += int(usage.get(k_in) or 0)
        self._usage.tokens_out += int(usage.get(k_out) or 0)


class AnthropicClient(_Base):
    provider = "anthropic"

    def next_action(self, view: dict[str, Any]) -> Any:
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": self._system,
            "messages": [{"role": "user", "content": self._user_text(view)}],
            "tools": [
                {
                    "name": f["name"],
                    "description": f["description"],
                    "input_schema": f["parameters"],
                }
                for f in FUNCTIONS
            ],
            "tool_choice": {"type": "any", "disable_parallel_tool_use": True},
        }
        headers = {"x-api-key": self._key, "anthropic-version": "2023-06-01", "User-Agent": UA}
        body = self._send(ANTHROPIC_URL, headers, payload)
        usage = body.get("usage") or {}
        self._usage.calls += 1
        self._usage.tokens_in += int(usage.get("input_tokens") or 0)
        self._usage.tokens_out += int(usage.get("output_tokens") or 0)
        calls = [b for b in body.get("content") or [] if b.get("type") == "tool_use"]
        if not calls:
            return to_raw_action(None, None)
        return to_raw_action(calls[0].get("name"), calls[0].get("input"), len(calls))


def make_client(provider: str, model: str, api_key: str, **kw: Any) -> _Base:
    if provider == "anthropic":
        return AnthropicClient(model, api_key, **kw)
    return OpenAICompatClient(provider, model, api_key, **kw)
