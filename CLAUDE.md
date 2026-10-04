# MoodMeals: rules for Claude Code

MoodMeals is a portfolio project: a "kya khana hai, batao" agent that decides
between cooking (Swiggy Instamart) and ordering in (Swiggy Food).

Read first, in this order: `specs/requirements.md`, `specs/design.md`, `specs/tasks.md`.

## Rules

- The specs are the source of truth. If a spec looks wrong or unclear, tell the
  owner and stop; do not silently deviate.
- Work one task at a time, in the order in `specs/tasks.md`. Do only what the
  current prompt asks.
- Stack: Python 3.11+, Streamlit, official `mcp` SDK, httpx, pydantic, pytest,
  ruff. Hand-written agent loop, no agent framework.
- Never write real personal data into the repo: no addresses, phone numbers,
  names of other people, order history, or API keys. Fixtures must be synthetic.
- Never call any Swiggy write tool (cart, order, checkout, address changes) or
  any payment tool. Read-only only.
- Keep the core package (`moodmeals/`) UI-independent (design.md section 2):
  no Streamlit imports outside `app/`.
- Commit small, with clear messages. Committing directly to `main` is fine for now.

## Commands

```
pip install -e ".[dev]"
ruff check .
pytest
```
