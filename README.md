# MoodMeals

A "kya khana hai, batao" agent. It takes a vague "I don't know what to eat" and
turns it into one concrete plan: **cook at home** (Swiggy Instamart) or
**order in** (Swiggy Food). It asks at most three questions, recommends one
plan, and stops at an approval gate before anything with real-world effect.

This is a personal portfolio project. It is **not** affiliated with, approved
by or endorsed by Swiggy.

**Status:** early build (Phase 0). Nothing is runnable as a product yet.

## Specs

The specs are the source of truth:

- [specs/requirements.md](specs/requirements.md)
- [specs/design.md](specs/design.md)
- [specs/tasks.md](specs/tasks.md)

## Layout

```
moodmeals/   core package (UI-independent): core, models, providers, tools
app/         Streamlit pages
evals/       scenarios, runner, results
data/        recorded replays (mock data only)
spikes/      one-off experiments (see spikes/README.md)
tests/
```

## Development

Python 3.11 or newer.

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
ruff check .
pytest
```
