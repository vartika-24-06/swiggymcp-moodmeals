"""App configuration and the startup mode check (R10.5). No Streamlit imports here."""

from __future__ import annotations

import os

DISCLAIMER = "Planning aid, not advice. It suggests a meal; it does not diagnose or advise."
PASS_THROUGH_NOTICE = (
    "On this public site your key passes through the app's server to the model provider you "
    "choose. It is kept in memory for this session only and is never saved or logged. "
    "Use a key with a low spend limit, or run the app on your own machine."
)
PROVIDERS = {
    "Scripted demo (no key)": "demo",
    "OpenAI": "openai",
    "Groq": "groq",
    "OpenRouter": "openrouter",
    "Anthropic": "anthropic",
}
SUGGESTED_MODELS = {
    "openai": "gpt-5-mini",
    "groq": "openai/gpt-oss-20b",
    "openrouter": "",
    "anthropic": "claude-haiku-4-5-20251001",
}


class ModeRefused(RuntimeError):
    pass


def is_public(env: dict[str, str] | None = None) -> bool:
    return (env if env is not None else os.environ).get("MOODMEALS_DEPLOY", "local") == "public"


def resolve_mode(env: dict[str, str] | None = None) -> str:
    """The public deployment runs mock mode only; anything else is refused at startup."""
    e = env if env is not None else os.environ
    requested = e.get("MOODMEALS_MODE", "mock")
    if requested not in ("mock", "dry_run", "live"):
        raise ModeRefused(f"Unknown mode: {requested}")
    if is_public(e) and requested != "mock":
        raise ModeRefused("The public site runs mock mode only.")
    if requested == "live":
        raise ModeRefused("Live mode arrives with its guard rails (T6.3). Use dry_run.")
    return requested
