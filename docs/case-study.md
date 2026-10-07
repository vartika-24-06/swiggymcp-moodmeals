# MoodMeals: a case study

*Draft for the owner to edit. First person is the owner's; check every claim against the repo
before publishing. Not affiliated with, approved by or endorsed by Swiggy.*

## The problem

"Kya khana hai?" is the most repeated question in a household and a hard one to answer. The
real choice is rarely "which dish". It is *effort*: cook something, grab a ready-to-eat meal
from Instamart, or order in from a restaurant. I wanted an agent that takes a vague mood and
makes that decision, explains it, and then stops short of anything irreversible.

Two Swiggy MCP servers made it concrete: **Food** (restaurants, menus, a cart) and **Instamart**
(groceries and quick meals). The agent has to choose between genuinely different paths, with
real tool data, not generate a recipe.

## Principles I set before writing code

1. **At most three questions.** A person who does not know what to eat will not fill in a form.
   The agent may ask, but the budget is enforced by code: the fourth question is refused and the
   model is told to decide and list its assumptions.
2. **The model decides; code verifies and enforces.** The model chooses the path, the
   restaurant, the dishes and the wording. Code owns parsing, totals, the question budget, run
   limits, the plan validator and the write gate. If a rule matters, it lives in code.
3. **Nothing with real-world effect without approval.** Three modes: *mock* (made-up world),
   *dry-run* (real read-only data, writes blocked and previewed) and *live* (opt-in, cart
   updates only, one approval per action). No order, checkout or payment tool exists in the
   codebase at all.
4. **The model never sees personal data.** Tool results reach it as a whitelisted view marked
   untrusted; addresses are handles the UI resolves, not text.
5. **Publish failures.** If I only show the runs that worked, the project proves nothing.

## What the agent decides, and what code decides

| The model | Code |
|---|---|
| Cook, grab a quick meal, or order in | Whether a tool call is allowed, how often, how long |
| Which searches to run | Parsing prices, veg flags, stock, opening hours |
| Which restaurant and items | Whether every item exists and the total is right |
| What to ask, in the person's language | Whether the question budget is spent |
| The reason shown to the person | Whether the budget, diet and party size are respected (validator) |
| When to give up searching | Whether it has really tried both Food and Instamart first |

The loop is a hand-written state machine with four model actions: call a tool, ask a question,
propose a plan, stop the search. I used no agent framework, partly because the loop is small
and partly because I wanted every rule visible in one place.

## What went wrong, and what it changed

These are the failures that changed the code, in the order I met them.

- **Padded cook plans.** The first prompt produced 7 to 10 items for one person (oil, salt,
  ghee). Fixed in the prompt: pantry basics are listed as an assumption, not bought.
- **A harness bug that looked like a product bug.** My scripted user answered budget questions
  nobody asked, so a ₹500 budget was never enforced in one scenario. Budget is now set up front.
- **Real data did not look like my mock.** The first dry-run against real Swiggy data found
  different address field names; the parser was fixed with a synthetic test, not by copying
  anything real into the repo.
- **A free-tier model rejected my requests as too large (HTTP 413).** Every earlier tool result
  was being resent on every turn. The model's view now keeps the three newest results in full
  and caps item counts. This also cut cost for every model.
- **Rate limits were scored as agent failures.** The first "agent failures" on a free tier were
  all `rate_limited`. The runner now paces, retries and marks such runs invalid, not failed.
- **A closed laptop became a failure.** Two runs recorded a single model call of 954 s and
  1,658 s. They are excluded and listed, and the runner now flags any call over three minutes.
- **My scorer was wrong, not the agent.** The model wrote "ordering in isn't possible" with a
  curly apostrophe; my word list missed it and scored three correct fallbacks as failures.
  Fixed, and re-scored from stored traces instead of re-running.
- **The agent gave up too early.** In S-04 it searched restaurants twice, then stopped without
  ever checking Instamart. Code now refuses `stop_search` until both Food and Instamart were
  tried. This is a rule moved from prompt to code.
- **A validator rule that never fired.** The full run showed S-11 (dinner for two, no budget)
  listing no assumptions. The cause was in my code: the list of details the person had not given
  was never filled in, so the validator rule was dead. Fixing it exposed a second bug: an
  answer of "no limit" counted as a stated budget. Both are fixed and tested.

## Results

24 scenarios on the made-up world: happy path (6), missing information (4), contradictions (3),
infeasible requests (3), tool failures (3), mid-run changes (3) and safety (2). Deterministic
checks score each run: right path, hard constraints, no invented item, question limit, plan
validity, assumptions listed, no write without approval, and more. Three strategies run on the
same scenarios with the same model (gpt-5-mini):

| Runs passing every check | |
|---|---|
| Agent | 71 of 71 |
| Agent, validator switched off | 67 of 71 |
| Fixed code-only workflow | 16 of 24 |

What I take from it, and what I do not:

- **The validator earns its place, narrowly.** Without it, three plans broke a rule (a budget
  after a mid-run drop, a "cheap but premium" request, a guest joining). The model did not
  invent items in any run. The catch is constraint and consistency errors, not hallucination.
- **Path choice earns its place.** The fixed workflow always orders in, so it fails every
  scenario where the right answer is to cook or where Food is unavailable.
- **Safety held.** No write ran without approval in any strategy, including S-23 (an
  instruction hidden in a dish name) and S-24 ("place the order, don't ask").
- **The honest caveat.** The agent's 71 of 71 is after I fixed its prompt and code for the
  failures the first full run showed (S-04, S-05, S-11), and I raised one scenario's limit
  (S-23) after seeing a reasonable plan fail it. The first full run scored 65 of 71. Both
  numbers are published, with the prompt version per scenario. The fixed workflow was not tuned.
  Three runs per scenario and one model is evidence, not a rate.

Cost: the whole eval programme, including reruns, was about ₹161 at ₹97 per dollar.

## What I would do next

- Run the same suite on a second and third model, including a small open one, and report the
  spread, since one model's 71 of 71 says little.
- Confirm the real Food cart reply with items in it; only the empty-cart shape has been seen.
- Add real-world scenarios from dry-run traces (kept out of the repo unless made synthetic).
- Revisit the Dineout "go out" path if Swiggy fixes the reported tool defects. The provider
  interface means it can be added without touching the loop.
- A held-out set of scenarios written after the prompt is frozen, so the final number is not
  also the number I tuned against.

## Limits, stated plainly

Swiggy's own documentation says third-party app development is "not permitted at this time",
while its Builders Club pages describe it; I followed the more specific pages and treat any
order as real. Prices in the cost table are unverified list prices. English and Hinglish only,
one person, one meal. Nothing here claims Swiggy approval or partnership.
