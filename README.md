# MoodMeals

**Live demo (mock data, no key needed):** https://swiggymcp-moodmeals-byvartika.streamlit.app/

**Demo video (real Swiggy data, read-only dry-run on my own machine):** https://drive.google.com/file/d/1f-2KqgMvKcHaOobi1U79c_BBmPKuCkIA/view?usp=sharing

The live site uses made-up restaurants and groceries only. The real Swiggy connection runs locally
(Swiggy sign-in allows only localhost), so it is shown in the video.

A "kya khaun, batao" agent. It takes a vague "I don't know what to eat" and turns it into
one concrete plan: **cook or grab it from Instamart**, or **order in from Swiggy Food**. It asks
at most three questions, recommends one plan, checks the plan against what the tools really
returned, and stops at an approval gate before anything with real-world effect.

This is a personal portfolio project. It is **not** affiliated with, approved by or endorsed by
Swiggy. Planning aid, not advice: it suggests a meal; it does not diagnose or advise.

## What it shows

- **A real decision between two different paths.** Cooking (Instamart groceries or a quick-meal
  item) versus ordering in (a restaurant dish). The agent has to pick one and say why.
- **A hand-written agent loop**, no agent framework: a state machine with four model actions
  (call a tool, ask a question, propose a plan, stop the search).
- **Code, not the model, enforces the rules.** A question budget of three, run limits, a plan
  validator (the model cannot invent an item, a price, a closed restaurant or a budget it was
  not given), and a write gate that needs one approval per action.
- **A privacy firewall.** The model sees a whitelisted view of tool results (as untrusted data),
  never addresses, phone numbers or names.
- **Failures are handled and published.** 24 eval scenarios, a no-validator ablation, and a
  results page that lists every failed run.

## Try it in five minutes (mock mode, no key, no Swiggy account)

Python 3.11 or newer.

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
streamlit run app/Home.py
```

Pick **Scripted demo (no key)**, type a mood, and press plan. Everything runs on made-up
restaurants and groceries. Other pages in the sidebar:

- **Replay**: recorded runs (successes and failures) you can step through with no model and no
  network.
- **Results**: the committed eval results.
- **About**: what this is and its limits.

To use a real model, choose a provider in the app and paste a key. The key is kept in memory for
the session and never saved or logged; use a key with a low spend limit.

## The three modes

| Mode | Data | Writes | How to start |
|---|---|---|---|
| **mock** (default, and the only mode on a public deployment) | Made-up world | None | `streamlit run app/Home.py` |
| **dry_run** | Your real Swiggy data, read-only | Blocked; the approval screen shows a preview and changes nothing | `MOODMEALS_MODE=dry_run streamlit run app/Home.py`, then "Connect to Swiggy" |
| **live** | Your real Swiggy data | Cart updates only, after your approval | also set `MOODMEALS_ALLOW_LIVE=1` |

Dry-run and live run only on your own machine, because Swiggy sign-in allows only localhost
redirects. `scripts/swiggy_check.py` makes a handful of read-only calls to check the connection
and prints counts and field names, never addresses or ids.

In live mode the code may update the cart (`update_food_cart`, `update_cart`) behind the write
gate: one approval per action, and a check for an existing cart first. **No order, checkout or
payment tool exists in this codebase**; you finish the order yourself in the Swiggy app. Cart
totals can differ from the plan because Swiggy recomputes them.

## Architecture

```
 Streamlit pages (app/)           Evals (evals/)
 Plan · Replay · Results · About  scenarios, runner, scoring
          │                              │
          └──────────────┬───────────────┘
                         ▼
              Core package (moodmeals/core)
   RunState + event log · agent loop (state machine) · guard and question budget
   plan validator · totals · WriteGate (mode + approvals)
           ┌─────────────┴─────────────┐
           ▼                           ▼
   LLMClient (moodmeals/models)   ActionProvider (moodmeals/providers)
   OpenAI-compatible · Anthropic   Mock · Swiggy (dry-run / live)
```

The core package has no Streamlit imports, so the same loop runs in the app, in tests and in
the eval runner. Full detail is in [specs/design.md](specs/design.md).

## Evals and results

`evals/scenarios/` holds 24 scenarios: happy path, missing information, contradictions,
infeasible requests, tool failures, mid-run changes and safety (including a prompt injection
hidden in a dish name). Each run is scored by deterministic checks, not by another model. Three
strategies are compared on the same scenarios: the agent, the agent with the validator switched
off, and a fixed code-only workflow.

Latest full run (gpt-5-mini, 24 scenarios, 3 runs per scenario for the agent strategies, about
₹161 including re-runs), runs passing every check:

| Strategy | Runs passing every check |
|---|---|
| Agent | 71 of 71 |
| Agent, validator off | 67 of 71 |
| Fixed workflow | 16 of 24 |

Three of the four validator-off failures are plans the validator would have rejected. No
strategy wrote anything without approval or invented an item. Read these with care:

- Samples are small (three runs per scenario, one model), so these are evidence, not rates.
- I wrote the scenarios and the scoring after seeing early failures, and I fixed the agent's
  prompt and code for three of them (S-04, S-05, S-11) before the final numbers. The merged file
  is labelled with the prompt version used for each scenario. The fixed workflow was not tuned.
- Everything ran on the made-up world. Real Swiggy data was only used for read-only checks and
  dry-run demos.
- Two runs where a model call stalled (a sleeping laptop) are excluded and listed.

The raw files are in [evals/results/](evals/results/README.md); the **Results** page shows every
failed run.

## Limits

- Swiggy's own documentation says third-party app development is "not permitted at this time"
  while its Builders Club pages describe it; this project follows the more specific pages and
  treats any order as real. It never calls a payment tool.
- Whether Swiggy's real Food cart reply, with items in it, matches what the code expects is
  unconfirmed; only the empty-cart shape has been observed.
- Prices in the cost table are unverified list prices; the exchange rate (₹97 per dollar) is
  set by the owner.
- English and Hinglish only, one person, one meal.

## Why no "go out" path (Dineout)

An early plan had a third path, going out to eat via Swiggy Dineout. It was dropped for v1:
public issues on Swiggy's manifest repository report a cancel tool that is not served and a
slots result that exists only as prose; four read-only searches returned nothing with no error;
and its booking step is created and confirmed in one go and cannot be undone through the tools.
Building on that would have meant guessing. The reasons and the conditions for revisiting are in
[specs/requirements.md](specs/requirements.md) section 12.1. The provider interface means a
Dineout provider could be added later without changing the loop.

## Development

```
pip install -e ".[dev]"
ruff check .
pytest
```

The specs are the source of truth: [requirements](specs/requirements.md),
[design](specs/design.md), [tasks](specs/tasks.md).

```
moodmeals/   core package (UI-independent): core, models, providers, tools
app/         Streamlit pages
evals/       scenarios, runner, scoring, results
data/        recorded replays (mock data only)
prompts/     versioned agent prompts
scripts/     swiggy_check, merge_results, make_replays
spikes/      one-off experiments (see spikes/README.md)
tests/
```

To re-run evals you need a model key in your terminal (never in the repo):
`python -m evals.runner --set full --provider openai --model gpt-5-mini --runs 3`.
