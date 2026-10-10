# MoodMeals

**Live demo (mock data, no key needed):** https://swiggymcp-moodmeals-byvartika.streamlit.app/
**Demo video (real Swiggy data, read-only dry-run on my machine):** https://drive.google.com/file/d/1f-2KqgMvKcHaOobi1U79c_BBmPKuCkIA/view?usp=sharing

*A "kya khaun, batao" agent that decides between cooking and ordering in.*

Not affiliated with, approved by or endorsed by Swiggy. Planning aid, not advice.

## The problem

"Kya khaun?" is a decision problem, not a search problem. The hard part isn't finding a dish.
Choosing the *kind* of meal comes first, and that is mostly about effort: cook something, grab a
ready-to-eat item from Instamart, or order in from a restaurant. With too many options, people
end up ordering nothing or scrolling for 30 minutes.

MoodMeals takes a vague "thaka hua hoon, kuch halka" and turns it into one concrete plan, with
the reason, the price and a stop for approval. It asks at most three questions.

## Why I built it, and why agentic

I'm a PM, and I wanted to learn agentic AI by building one properly, not by reading about it.
Swiggy's Food and Instamart MCP tools made it possible: real restaurants, real menus and real
stock, so the agent can't just make things up.

**Why agentic here:** the number of steps isn't known up front. If every restaurant is closed,
the agent has to switch to Instamart. If the person drops their budget mid-run, it has to
replan. If a search returns nothing, it has to try something else. A fixed script handles the
happy path; the agent decides what to try next.

**An honest note.** Much of this flow could also be built as a router plus a pipeline, and I
didn't build that baseline. What I compared against is a simple code-only workflow that always
orders in. So the results show the agent beating a naive script, not beating a good pipeline.
Most of the reliability came from code around the model, not from the model.

## How I built it: spec first

I wrote the requirements, design and task list before any code (`specs/`), and treated them as
the source of truth. Work went one task at a time, with the spec updated whenever reality
disagreed.

The specs changed in useful ways:

- I dropped the "go out" (Dineout) path after finding open tool defects and getting empty
  results from every search. Details are in `specs/requirements.md`, section 12.1.
- I added the quick-meal fallback after a scenario showed the agent giving up too early.
- An eval run exposed a bug where a validator rule could never fire because one input was never
  filled in.

## The design in one paragraph

A hand-written agent loop with no framework. The model picks one of four actions per turn: call a
tool, ask a question, propose a plan, or stop searching. **Code enforces the rules:** at most
three questions, run limits, a plan validator that checks every item and price against real tool
results, and a write gate that needs one approval per action. The model never sees addresses or
phone numbers, and no order, checkout or payment tool exists in the codebase. Full detail is in
[specs/design.md](specs/design.md).

## Running it

### Option 1: the live link (mock only)

Open https://swiggymcp-moodmeals-byvartika.streamlit.app/, pick **Scripted demo (no key)**, type
a mood and plan. You can also paste your own model key to try a real model on made-up data. The
The Demo video, Replay and Results pages are in the sidebar.

**This site can only run mock mode, with made-up restaurants and groceries.** It cannot connect
to Swiggy. Swiggy's sign-in only allows redirects to localhost, so a hosted site can't complete
it, and I wouldn't route anyone's Swiggy account through a server anyway.

### Option 2: locally (mock, dry-run or live)

Python 3.11 or newer.

```
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
streamlit run app/Home.py       # mock mode
```

For the real Swiggy connection, read-only, nothing is changed:

```
$env:MOODMEALS_MODE="dry_run"   # macOS/Linux: export MOODMEALS_MODE=dry_run
streamlit run app/Home.py
```

Press **Connect to Swiggy** and sign in to Food, then Instamart. Approving a plan shows a
preview and sends nothing to Swiggy.

**Live mode** (cart updates only, behind an approval) exists but is opt-in, with
`MOODMEALS_ALLOW_LIVE=1`. No order or payment tool exists.

## Why the demo is a video

Because of that localhost restriction, the real-data run exists only on my machine. The **demo
video** shows dry-run against my own Swiggy account (personal details cut out), including the
approval screen where nothing is sent.

## How I tested it

24 scenarios on the made-up world: happy paths, missing information, contradictions,
infeasible asks, tool failures, mid-run changes and safety (including an instruction hidden in a
dish name). Scoring is by deterministic checks, not another model. All results, including
failures, are on the app's **Results** page and in [evals/results/](evals/results/README.md).

Latest run (gpt-5-mini, about ₹161 including reruns), runs passing every check:

| Strategy | Passing |
|---|---|
| Agent | 71 of 71 |
| Agent, validator switched off | 67 of 71 |
| Fixed code-only workflow | 16 of 24 |

- The validator caught three plans that broke a rule. No strategy invented an item or wrote
  without approval.
- **Read it with care:** one model, three runs per scenario, and the first run scored 65 of 71.
  I then fixed the prompt and code for three scenarios (S-04, S-05, S-11) and raised one
  scenario's limit (S-23). The agent was tuned against its own scenarios.

## Limits

- Results come from one model and small samples.
- The agent tuning above means 71 of 71 is not a held-out score.
- The real Food cart reply with items hasn't been confirmed.
- Model prices are unverified list prices.
- English and Hinglish only, one person, one meal.
- Swiggy's own docs say third-party app development is "not permitted at this time", while its
  Builders Club pages describe it. I followed the more specific pages and treat any order as
  real.

## What I'd do next

Test a second and third model; build the router baseline; write a held-out scenario set after
freezing the prompt; confirm the real Food cart shape.

## Repo map

`specs/` (requirements, design, tasks) · `moodmeals/` (core, UI-free) · `app/` (Streamlit) ·
`evals/` (scenarios, runner, results) · `prompts/` · `data/replays/` · `docs/` (case study,
deploy guide, demo script)

Development checks: `ruff check .` and `pytest`.
