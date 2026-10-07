# MoodMeals: Design

**Status:** Draft v0.2 · **Date:** 2026-10-04
**Implements:** requirements.md v0.7 (R1–R5, R7–R15)
**Stack (proposed, verify versions at build time):** Python 3.11+, Streamlit, official `mcp` Python SDK, `httpx`, `pydantic`, `pytest`

Everything marked *(proposed)* is a starting choice to revisit during the build. Section 14 lists open design questions.

---

## 1. Design goals

1. **The model decides, the code enforces.** The model chooses the next step and proposes a plan. Deterministic code owns every limit, check and write.
2. **A model can't leak or spend what it never sees.** Personal data never reaches the model, and write tools are not reachable without an approval record.
3. **Everything is replayable.** One event log feeds the live trace, the replay gallery and the evals.
4. **Same agent everywhere.** The mock, dry-run and live modes differ only in which provider is plugged in.
5. **Small enough to finish.** Two paths, a handful of tools, one loop.

## 2. Architecture

```
 Streamlit pages (app/)          Evals (evals/)
 Plan · Trace · Replay · Evals   scenarios, runner, scoring
          │                              │
          └──────────────┬───────────────┘
                         ▼
              Core package (moodmeals/core)
   ┌───────────────────────────────────────────────┐
   │ RunState (serialisable)   Event log            │
   │ Agent loop (state machine)                     │
   │ Guardrails · Question budget                   │
   │ Plan validator · Totals (pure functions)       │
   │ WriteGate (mode + approval records)            │
   └───────┬───────────────────────────┬───────────┘
           ▼                           ▼
   LLMClient (moodmeals/models)   ActionProvider (moodmeals/providers)
   OpenAI-compatible · Anthropic   Mock · Swiggy (dry-run / live)
```

**Layers and what each may know**

| Layer | Knows | Must not know |
|---|---|---|
| Streamlit pages | RunState, events, how to render | Model prompts, tool internals |
| Agent loop | State, tool catalogue, guardrails | UI, provider specifics |
| LLMClient | Message format of one vendor | Anything about Swiggy |
| ActionProvider | How to call tools and normalise results | The model, the UI |
| WriteGate | Mode and approval records | Nothing else; it is a small standalone gate |

**Package layout (proposed)**
```
moodmeals/
  core/        loop.py state.py events.py guardrails.py validator.py
               totals.py parsing.py writegate.py checkin.py
  models/      base.py openai_compat.py anthropic.py pricing.py
  providers/   base.py mock.py swiggy.py fixtures/
  tools/       catalogue.py schemas.py (tool specs shown to the model)
app/           Home.py pages/ (Plan, Replay, Evals, About)
evals/         scenarios/ runner.py scoring.py results/
data/replays/  recorded runs (mock data only)
tests/
```

## 3. Modes and what runs where

| Mode | Provider | Writes | Where it runs |
|---|---|---|---|
| mock | Mock fixtures | simulated in memory | public deployment, evals, replay generation |
| dry-run (default for Swiggy) | Swiggy, real read-only data | blocked and previewed | owner's machine (`streamlit run`) |
| live | Swiggy | allowed per action, with approval, opt-in per session | owner's machine only |

- The mode is an environment setting read once at start (`MOODMEALS_MODE`), never a model decision.
- The public deployment refuses to start in dry-run or live (R10.5).
- **Dry-run disclaimer (R10.8), shown on every dry-run plan:** "Item total only. Delivery fees, taxes and discounts are not shown in dry-run. The final bill is shown only in live mode, before you place an order."
- **Dry-run limitation to state honestly:** the real bill (delivery, taxes, fees) only appears after a cart update, which is a write. In dry-run, plans show an **item total** and say the final bill is unknown. In live mode, an approved cart update reveals the real bill before the order step.

## 4. The agent loop

### 4.1 States
```
CHECKIN → GATHER → PROPOSE → VALIDATE → AWAITING_APPROVAL → EXECUTE → DONE
              ↑        │          │              │
              └────────┴──────────┘              └→ (rejected) → PROPOSE
         any state → STOPPED (guardrail, cancel, unrecoverable error)
```

| State | What happens | Who acts |
|---|---|---|
| CHECKIN | Model may ask at most 3 questions (address counts). | Code + model |
| GATHER | Model calls read tools to look at restaurants, menus, products. | Model, within limits |
| PROPOSE | Model submits a structured plan for one path with a one-sentence reason. | Model |
| VALIDATE | Validator checks the plan; on failure the errors go back to the model, max 2 retries. | Code |
| AWAITING_APPROVAL | Loop is paused. UI shows exactly what the action will do. | User |
| EXECUTE | WriteGate allows or blocks the write per mode and approval. | Code |

### 4.2 What the model may output each turn
Exactly one of four action types (structured, validated by code):
1. `tool_call(name, params)`: a read tool from the catalogue for the current state.
2. `ask_user(question, options?)`: refused by code once the question budget is spent.
3. `propose_plan(path, reason, items[], assumptions[])`: moves to VALIDATE.
4. `stop_search(reason)`: gives up with a one-sentence reason; refused (protocol error) until at least one tool call has been made (R4.3).

Anything else is a protocol error: the model gets one corrective message, then the run stops cleanly.

### 4.3 Loop sketch
```python
def step(state: RunState, llm: LLMClient, provider: ActionProvider) -> RunState:
    guard.check(state)                       # iterations, tool calls, time, cancel
    if state.phase == "AWAITING_APPROVAL":   # paused: nothing to do until resume()
        return state
    action = llm.next_action(state.model_view())   # PII-free view of state (section 6)
    if action.kind == "tool_call":
        result = provider.call(action.name, action.params)     # reads only here
        state.add_tool_result(action, summarise(result))
    elif action.kind == "ask_user":
        state.ask(action) if budget.allows() else state.refuse_question()
    elif action.kind == "propose_plan":
        errors = validate(action.plan, state.ledger)
        state.accept_plan(action.plan) if not errors else state.retry(errors)
    return state

def resume(state: RunState, decision: Approval) -> RunState:
    # called by the UI when the user approves or rejects one specific action
    return execute_or_reject(state, decision)   # goes through WriteGate
```
The loop is a **resumable state machine** over a serialisable `RunState`, not one long blocking function. This is what makes the approval gate work in Streamlit (section 11).

### 4.4 Who decides what (autonomy table)

| Decision | Model | Code | User |
|---|---|---|---|
| Which path (cook or order in) and why | proposes | validates against the plan rules | can ask for another idea |
| Which tool to call next | chooses | enforces catalogue, limits, mode | |
| What to ask | chooses wording | enforces budget (max 3) and address rule | answers |
| Which address to use | never sees it | ranks and displays | chooses |
| Prices, totals, unit prices | quotes | computes and compares (tolerance 0) | |
| Veg and budget compliance | proposes items | checks hard constraints | |
| Whether to place a write | proposes | blocks without approval | approves one action at a time |
| When to stop | may propose "can't verify" | stops at limits | cancels |

## 5. Tool layer

### 5.1 Tools shown to the model (normalised names)
Tiers: **read** (free to call), **amber** (cart changes, reversible), **red** (places an order). The model never sees payment tools, address-changing tools or order-history tools (R10.7, R12).

| Normalised tool | Swiggy tool | Tier | Notes |
|---|---|---|---|
| `list_addresses` | Food `get_addresses` | read | **Called by code, not the model.** Result shown to the user only. |
| `search_restaurants(query, offset)` | Food `search_restaurants` | read | Address handle is injected by code. |
| `get_menu(restaurant_id, page)` | Food `get_restaurant_menu` | read | Compact view; paged. |
| `search_dish(query, restaurant_id)` | Food `search_menu` | read | Needed for variants and add-ons before a cart update. |
| `search_products(query, offset)` | Instamart `search_products` | read | Address handle injected by code. |
| `update_food_cart(items)` | Food `update_food_cart` | amber | Live only, per approval. |
| `update_cart(items)` | Instamart `update_cart` | amber | Replaces the whole cart; code asks before replacing (DQ6). |
| `place_food_order()` | Food `place_food_order` | red | **Not implemented in v1** (R10.9): the person orders in the Swiggy app. |
| `checkout()` | Instamart `checkout` | red | **Not implemented in v1** (R10.9). |
| `get_cart_state(cart)` | Food `get_food_cart` / Instamart `get_cart` | read | Code only. Returns only `{"empty": bool}`; contents never leave the provider. |

Not exposed in v1: payment options, payment status, confirm order, create or delete address, order history, "go-to items" (unless the user permits, R7.2), coupon tools *(proposed: add later as read-only)*.

### 5.2 Normalised data types
```python
class Restaurant:  id, name, cuisines, rating, rating_count:int, cost_for_two:int,
                   distance_km, eta_minutes, open:bool, sponsored:bool
class MenuItem:    id, restaurant_id, name, price:int, veg:Veg, in_stock:bool,
                   has_variants, has_addons
class Variant:     spin_id, label, price:int, mrp:int, max_qty:int
class Product:     id, name, brand, variants:list[Variant], veg:Veg, sponsored:bool,
                   eta_minutes
Veg = Literal["veg", "egg", "non_veg", "unverified"]   # unknown or invalid -> unverified
class PlanItem:    kind, entity_id, name, variant_id?, qty, unit_price:int
class Plan:        path, reason, items, item_total:int, assumptions, mode_notes
```

### 5.3 Parsing in code, not by the model (R5.5, R7.7)
Small pure functions with unit tests: `parse_count("5.1K+") -> 5100`, `parse_cost_for_two("₹400 for two") -> 400`, `parse_price`, `parse_eta("10-20 MINS")`, `map_veg_classifier(...)` (anything not clearly veg or non-veg becomes `unverified`), `strip_ad_marker("... (Ad)") -> (name, True)`. Tool results are compacted before the model sees them (section 14, DQ2).

## 6. Privacy design (the "PII firewall")

**Decision:** the model never receives address text, phone numbers, names of other people, order history or "buy again" badges.

- Code calls the address tool. The **UI** shows the list so the user picks one. The model receives only an opaque handle (`address_1`) and "address confirmed".
- Code injects the real address id into search calls, so the model passes no location at all.
- The model's view of state (`RunState.model_view()`) is built by a function that whitelists fields. Everything else is dropped by default.
- Event log and trace store the handle, never the text. Replays use mock data (R12.3).
- Tests assert that no model message, event or replay contains a string from the fixture address list or a phone-number pattern.
- OAuth tokens and model keys live in memory for the session only *(proposed; the owner re-signs in at each launch)*.

## 7. Providers

### 7.1 `ActionProvider`
```python
class ActionProvider(Protocol):
    mode: Literal["mock", "dry_run", "live"]
    def call(self, tool: str, params: dict) -> ToolResult: ...
class ToolResult:  ok:bool, data:dict|None, error:ToolError|None, latency_ms:int
```
- `MockProvider` serves a seeded synthetic world: restaurants, menus, products, with switches for failure injection (timeout, empty result, partial menu, out of stock, "(Ad)" entries, invalid veg classifier, max-quantity limits). The same shapes as the observed real responses (requirements A4, A5).
- `SwiggyProvider` wraps the MCP client. It normalises results into section 5.2 types. In dry-run it **refuses** amber and red tools and returns a preview object instead.
- The provider is called only through `WriteGate` for amber and red tools.
- **Built in T2.1 to T2.3** (`moodmeals/providers/`: `base.py`, `world.py`, `switches.py`, `mock.py`). Providers return payloads in the **raw shapes Swiggy's tools return** (observed 2026-10-04), and the tool layer converts them with `moodmeals.tools.normalise`, so the mock exercises the same parsers as the real provider. The world is a pure function of the seed; the switches (`Switches`) are `fail_tools` (timeout or error), `empty_search`, `partial_menu`, `all_closed`, `out_of_stock`, `ad_rate`, `invalid_veg_rate`, `max_qty`, `buy_again_badges` and `price_scale`. **Assumption to confirm:** the keys inside a mock address (`id`, `category`, `tag`, `address`, `phone`) are guesses, because the shape report does not read address fields. They only matter for the picker in T3.3.

### 7.2 `WriteGate`
A small standalone class, tested on its own:
```python
class WriteGate:
    def execute(self, action: WriteAction, approval: Approval | None) -> WriteOutcome:
        # blocks unless: mode == "live" AND approval matches this exact action
        # (same tool, same params hash, not already used, not expired)
```
- One approval authorises one action; the params hash prevents "approve A, execute B".
- Dry-run returns a "would do" preview. Every call, allowed or blocked, is logged (R10.4).
- Built in T1.4 (`moodmeals/providers/gate.py`). Order of checks: forbidden tools (payment, address changes, coupons) always blocked; unknown tools blocked (fail closed); dry-run returns a preview and never calls the provider, even with an approval; otherwise the approval must match tool and params hash, be unused and unexpired. The approval is marked used **before** the call, so a failed write is never retried. **Mock mode** follows the same approval rules and then calls the in-memory `MockProvider`, so evals and demos exercise the real gate; this is stricter than "blocks unless live", not looser.

### 7.3 Swiggy connection (dry-run and live)
- Official `mcp` Python SDK, streamable HTTP, OAuth 2.1 with PKCE, localhost redirect.
- **Open spike (Q2):** confirm the SDK completes Swiggy's phone-and-OTP sign-in on localhost. Fallback: sign in through another MCP client for the demo recording.
- Servers used: Food and Instamart only (Dineout dropped, requirements D12).

### 7.4 `LLMClient`
```python
class LLMClient(Protocol):
    def next_action(self, view: ModelView) -> Action: ...      # parsed and validated
    def usage(self) -> Usage: ...                               # tokens in/out for cost
```
- Two adapters *(proposed)*: an OpenAI-compatible one (OpenAI, Groq, OpenRouter, Gemini through its compatible endpoint) and an Anthropic one. Each converts the model's native tool-calling into the four action types of section 4.2.
- A pricing table (`pricing.py`) turns token counts into a cost estimate shown before a run (R14.3). Prices are config, with a "last checked" date, and the UI says they are estimates.
- The key is held in the session and passed only to the chosen vendor (R12.5).

## 8. Plan validator

Pure functions, no model, unit-tested. Input: the proposed `Plan` and the `Ledger` (every normalised tool result seen in this run). Output: a list of structured errors (`code`, `message`, `entity`) fed back to the model, plus warnings shown to the user.

| Check | Rule | Req |
|---|---|---|
| V1 Entity exists | Every restaurant, dish, product and variant in the plan appears in the ledger from this run, with matching kind and restaurant | R8.1 |
| V2 Price match | Plan unit price equals the ledger price; `item_total` recomputed as sum of qty × unit price; tolerance 0 | R8.2, R7.7 |
| V3 Availability | Restaurant open, item in stock, qty ≤ variant max quantity, only returned pack sizes | R5.1, R7.5 |
| V4 Hard constraints | Budget (item total only, see section 3), veg (any `unverified` item fails a veg constraint), stated exclusions | R8.3, R7.4 |
| V5 Single basket | Order-in plan uses one restaurant; cook plan uses products only | R5, R7 |
| V6 Delivery estimate | Any ETA shown equals the tool's value | R5.6 |
| V7 Assumptions | If the question budget ran out with information missing, assumptions are listed | R1.5 |
| V8 Sponsored | `sponsored` computed from tool data; shown to the user; never part of the reason | R5.2, R7.6 |
| V9 Mode note | Dry-run plans carry the exact disclaimer from R10.8; no estimated fees or taxes appear | R10.8, section 3 |

**Retry flow:** fail → structured errors to the model → new proposal → re-validate. Max 2 retries, then the run ends with "I could not produce a verified plan" and the reasons (R8.4). The validator never edits a plan itself; it only accepts or rejects.

**Soft checks (warnings, not failures)** *(proposed)*: a portion sanity check (very few items for a large party), and a price-per-person note.

## 9. Check-in and path decision

### 9.1 Signals
A `Signals` object: craving or cuisine, energy (low/ok/high), willingness to cook (no/maybe/yes), time (minutes), budget (₹), dietary needs, party size, each with a source (`user_text`, `answer`, `default`). The model extracts from free text into this schema; code validates ranges and rejects bad values. Moods are never scored or labelled (R1.6, R2.1).

### 9.2 Question budget (code-enforced)
- A counter allows at most 3 `ask_user` calls. A 4th is refused with a message to the model: decide with assumptions.
- **Address:** if exactly one saved address exists, code uses it and tells the user which one (no question). If several exist, the UI shows the list and the user picks (counts as one question, R3.2). The default is never auto-selected.
- Priority order for the remaining questions: hard constraints (diet, budget) > time > party size > preferences (R1.4). Anything already in `Signals` is skipped (R1.3).
- Questions are quick-pick buttons wherever possible, with free text allowed.

### 9.3 Path decision
- The model proposes a path with a one-sentence `rationale`. The heuristics from R4.4 live in the system prompt as guidance, not as code, so evals can test whether the model follows them.
- "Another idea" re-enters GATHER with the previous plan marked as rejected (R4.2). An infeasible order-in path (nothing open, search failing, nothing within the constraints) triggers an offer of a ready-to-eat or quick-cook Instamart meal, with a one-sentence reason, or a `stop_search` with a one-sentence reason (R4.3, R9).

### 9.4 Prompt as a specification
The system prompt is a versioned file (`prompts/agent_vN.md`) with fixed sections, so it can be tested and diffed: **SCOPE**, **ALLOWED ACTIONS** (the four action types), **PROHIBITED ACTIONS**, **GROUNDING RULES** (only use entities from tool results), **TONE** (short, no medical talk), **ESCALATION** (when to stop and say it can't produce a verified plan). The prompt version is stored with every run and eval result.

## 10. Guardrails and safety

### 10.1 Run limits (R11) *(proposed values)*
```python
@dataclass(frozen=True)
class RunBudget:
    max_iterations: int = 12
    max_tool_calls: int = 20
    max_seconds: int = 90
    max_questions: int = 3
    max_validation_retries: int = 2
    tool_timeout_s: int = 15
```
- Checked at the start of every step. Cancel is a flag checked between steps.
- A breached limit ends the run in `STOPPED` and returns the best verified plan so far, or a plain explanation (R11.2).
- **To revisit:** `tool_timeout_s = 15` is too tight for real Swiggy. In Spike A (2026-10-04) a single `get_addresses` call took about 16 seconds (`spikes/notes.md`). Settle the value, and how it fits inside `max_seconds`, before the real provider is built.

### 10.2 Retry policy
- **Read tools:** retry once with the same parameters, then try an alternative (another restaurant, another query, or switch path) (R9.1).
- **Amber and red tools are never retried automatically.** A repeated order or cart write is a real-world duplicate.

### 10.3 (removed)
The crisis check was removed on 2026-10-04 (requirements section 12.2). MoodMeals is a food decision tool, not a wellbeing product. The number is kept so other references stay stable.

### 10.4 Prompt injection
Restaurant names, dish descriptions and other tool text are untrusted data.
- Tool results are passed to the model inside a clearly delimited data block, summarised to whitelisted fields.
- The model cannot trigger a write directly: it can only `propose_plan`, which goes through the validator, the approval screen and `WriteGate`.
- Tool parameters are structured and schema-checked (length and character limits); free user text is never passed through to a tool (R11.3).
- Evals include a scenario where a dish description contains an instruction.

### 10.5 Spend
A `CostMeter` multiplies token usage by the pricing table and is shown per run (R13.2). The eval runner aborts at the configured cap (default ₹1,500, E2).

## 11. Streamlit design

Streamlit reruns the script on every interaction, so state and control flow follow three rules:
1. **All run state lives in one serialisable `RunState`** in `st.session_state` (nothing in module globals, no `st.cache` for keys or run data).
2. **The loop is driven as a generator** (`run_until_pause(state)` yields events) and the page renders events as they arrive into `st.status` and placeholder containers.
3. **Approval is a paused state, not a blocking call.** Approve and Reject are button callbacks that set the decision and call `resume(state, decision)`.

**Pages**

| Page | Purpose |
|---|---|
| Plan | Prompt, quick-pick questions, address picker, plan card, approval card, "another idea", cancel, trace panel |
| Replay | Recorded runs: step through the event log with no model or tool calls (R15.1) |
| Evals | Charts and tables from committed results; a failure gallery |
| About | Limits, disclaimer, privacy, cost, no-endorsement notice (R15.3) |

**Other points**
- The API key field is a password input kept in session state only, with the pass-through notice before pasting on the public deployment (R12.5).
- The public deployment is mock-only; the startup check enforces it (R10.5).
- A cancel click takes effect between steps; tool timeouts bound how long a step can run.
- No shared mutable state between users. The mock world is built per session from a seed.

## 12. Trace and replay

### 12.1 Event schema
```python
class Event:  run_id, step, ts, type, actor, payload, tokens_in, tokens_out, latency_ms
# type: user_input | question | tool_call | tool_result_summary | validation | plan
#       | approval | write_blocked | write_executed | stop | error
# actor: user | model | code
```
- `rationale` is a one-line justification the model attaches to each action, shown in the trace. Raw hidden reasoning is not stored or shown.
- A redaction function runs when an event is created, not when it is displayed: addresses, phones, order history and "buy again" badges never enter the log (R12, R7.2).
- The same event list feeds the live trace, the replay gallery and the eval scorer.

### 12.2 Replay files
`data/replays/<id>.json`: events, mock seed, model name, prompt version, date, outcome. Only mock-mode runs can be exported. The Replay page steps through events with play, step and speed controls, and never calls a model or tool. At least 6 recorded runs including 2 failures (section 11 of the requirements).

## 13. Evaluation harness

### 13.1 Scenario format
```yaml
id: S-07
group: infeasible
world: {seed: 7, restaurants_open: 0, failures: []}
user_script:                      # scripted answers, no second model
  opening: "no idea what to eat, tired"
  answers: {diet: "veg", budget: 400, address: "pick_first"}
expect:
  path_any_of: [cook, order_in]
  max_questions: 3
  hard_constraints: {veg: true, budget: 400}
  trajectory:
    must_call: [search_restaurants]
    must_not_call: [place_food_order, checkout]
  final: {verified_plan_or_clear_stop: true}
```

### 13.2 Strategies
| Strategy | What it is |
|---|---|
| `one_shot` | Model answers from the prompt with no tools. The validator is applied afterwards to measure hallucinated entities. |
| `fixed_workflow` | Code always runs: order in, search, pick by a simple rule, menu, choose items within budget. The model only extracts signals. |
| `agent` | The full loop. |
| `agent_no_validator` | The loop with the validator turned off, to show what it catches. |

### 13.3 Scoring (deterministic wherever possible)
Hard-constraint satisfaction and hallucinated-entity counts are computed by the validator logic. Questions asked and writes-without-approval come from the event log and `WriteGate`. Path appropriateness uses an expected-set per scenario, with "either" where both are defensible, so there is no model judge in v1. Failure handling counts replans or clean stops. Cost and latency come from the events.

### 13.4 Running and reporting
- 24 scenarios × 3 runs, low temperature *(proposed 0–0.2)*, model settings stored with results.
- Staged: smoke run (6 scenarios × 1 run) first, then the full set only if the smoke run looks sound and the budget allows.
- Results are written to `evals/results/<date>-<model>.json` with the git commit and prompt version, and committed. The dashboard reads those files and never calls a model.
- Report counts as "k of n", publish failures, and make no calibration claims (E1, E3).

## 14. Open design questions

| ID | Question | When to settle |
|---|---|---|
| DQ1 | Can the Python MCP client complete Swiggy sign-in on localhost? (requirements Q2) | **Resolved 2026-10-04: yes.** Spike A (T0.2) signed in with the `mcp` SDK 2.3.0 using dynamic client registration, PKCE and a `localhost:8765/callback` redirect, then made a read-only call. No sign-in fallback is needed. Details in `spikes/notes.md` |
| DQ2 | **Result compaction:** how much of each tool result the model sees (top N restaurants, menu caps, whitelisted fields) to keep context small and cheap | **Settled 2026-10-05** (`spikes/notes.md`, Spike C). The model sees only the whitelisted view built from the normalised types (`moodmeals/tools/compact.py`). Measured on real responses it cuts 63 to 86 percent (restaurant search about 450 tokens, dish search about 355, product search about 1,175). One menu page is still about 2,740 tokens, so menus are capped (proposed: first 30 items passing the constraints, plus the total count) |
| DQ3 | **Tool-calling reliability on free or cheap models.** Test 2–3 models early; decide a minimum tier and a recommended-model list; add a JSON fallback if needed | **Partly settled 2026-10-04** (`spikes/notes.md`, Spike B). On Groq free-tier models (gpt-oss-20b, qwen3.8-27b), native function calling gave a valid single action on 30 of 30 calls; JSON-in-text was clearly worse (22 of 29). So: build on native calling, no JSON fallback yet. Counts are small (3 repeats). The plan-building step is the weak spot and is re-measured in the real loop. OpenAI gpt-5-mini (parallel calls off) was valid and right on 15 of 15 but slow (median about 4 s per call). Gemini was not scored (503 and stalls when tested), so it is not on the recommended list yet |
| DQ4 | **Cook path:** how a simple meal becomes products (model proposes a meal; each ingredient searched; unavailable items substituted or dropped; at most ~6 ingredients *(proposed)*) | During the cook-path build |
| DQ5 | **Order-in menu depth:** prefer a dish search scoped to one restaurant over paging whole menus | During the order-in build |
| DQ6 | **Existing carts:** Instamart `update_cart` replaces the whole cart. In live mode code must read the current cart first and warn before replacing. The cart's contents stay out of the model's view (only "empty" or "not empty") | Before any live write |
| DQ7 | Hinglish input: the model handles it; add Hinglish prompts to the eval scenarios | With Q5 |

## 15. Design decisions

| ID | Decision | Status |
|---|---|---|
| DD1 | The model decides next steps; deterministic code owns limits, checks and writes (section 4.4) | Proposed |
| DD2 | Resumable state machine over a serialisable `RunState`, driven by a generator | Proposed |
| DD3 | The model outputs one of four action types per turn (a fourth, `stop_search`, was added 2026-10-05 for R4.3) | Proposed |
| DD4 | **PII firewall:** the model never sees addresses, phones, other people's names, order history or cart contents | Locked |
| DD5 | `WriteGate` approvals bind to one exact action (params hash); no automatic retries of writes | Proposed |
| DD6 | Dry-run shows an item total only, with a disclaimer that the final bill is shown only in live mode | Locked |
| DD7 | OAuth tokens and model keys are memory-only; the owner re-signs in at each launch | Locked |
| DD8 | Evals use a scripted user and deterministic scoring; no model judge in v1 | Proposed |
| DD9 | One action per turn: the model client asks providers for serial tool calls (`parallel_tool_calls` off, Anthropic `disable_parallel_tool_use`). Reason (Spike B): gpt-5-mini batched 7 to 10 searches in one turn, which breaks the one-action contract and would burn the 20-call budget. With it off, gpt-5-mini was valid and right on 15 of 15. Providers that cannot turn it off (Gemini): the loop must still handle several calls, by rejecting the turn with one corrective message (design 4.2) | Proposed |

## 16. Error handling

| Situation | Response | Req |
|---|---|---|
| Tool timeout or error (read) | Retry once, then an alternative, the quick-meal Instamart offer, or `stop_search` with a reason; trace both | R9.1, R4.3 |
| Tool returns nothing | Treat as "nothing found"; never invent; try alternative | R9.1 |
| Partial or malformed data | Drop the unusable entries; if nothing usable remains, treat as nothing found | R9.1 |
| Validator failure | Errors to the model, max 2 retries, then clear stop | R8.4 |
| Model protocol error (bad output) | One corrective message, then stop cleanly | section 4.2 |
| Model API error (bad key, rate limit) | Plain message to the user; no retry loop; run ends | N5 |
| OAuth expired | Prompt to sign in again; read-only runs can continue in mock | section 7.3 |
| Cancel or limit reached | `STOPPED` with the best verified plan or an explanation | R11.2 |
| Write requested in dry-run | Blocked and previewed; logged | R10.2, R10.4 |

## 17. Test plan

- **Unit:** parsing functions, validator checks, `WriteGate` (approve, mismatch, reuse, expiry, dry-run), guardrails, question budget, redaction.
- **PII tests:** no fixture address text or phone pattern appears in any model message, event or replay.
- **Loop tests with a `FakeLLM`** that replays scripted actions, so the full state machine runs with no model cost.
- **Mock world tests:** every failure switch produces the behaviour in section 16.
- **Injection test:** a dish description containing an instruction does not change tool calls or reach a write.
- **Eval smoke run** as the last gate before a full run.

## 18. Traceability

| Requirement | Where in this design |
|---|---|
| R1 check-in | 4.1, 9.1, 9.2 |
| R2 safety | 10.3, 9.1 |
| R3 location | 6, 9.2 |
| R4 path decision | 9.3, 9.4 |
| R5 order-in | 5.1, 5.3, 8 |
| R6 removed | not designed (requirements D12) |
| R7 cook | 5.1, 5.3, 8, DQ4, DQ6 |
| R8 validator | 8 |
| R9 replanning | 10.2, 16 |
| R10 approval and modes | 3, 7.2, 4.1 |
| R11 guardrails | 10.1, 10.2, 10.4 |
| R12 privacy | 6, 12.1 |
| R13 trace | 12 |
| R14 model providers | 7.4 |
| R15 replay and demo | 12.2, 13, 11 |
| N1–N5 | 3, 11, 16 |

### Phase 3 implementation notes (2026-10-05)

- The loop is `moodmeals/core/loop.py` (`Agent`): `run_until_pause` yields events and returns at a question, address pick, approval or end. The UI calls `provide_answer`, `choose_address`, `approve`, `reject` or `change_constraints`, then resumes.
- The model view is a whitelist (`core/view.py`); tool results sit under `untrusted_data`. Address text is held in private attributes and is not serialised.
- Cart and order are two separate approvals (R10.3). In dry-run, the first approval ends the run with a preview and nothing is written.
- Plans are built by code from the ledger; the model supplies ids and quantities only.
- Write tools are not in the model's action set: a `tool_call` for one is a protocol error; a second consecutive protocol error stops the run.

### Phase 6 implementation notes (2026-10-05)

- `providers/mcp_connection.py` is the real MCP link (sign-in as in Spike A; tokens in memory only). `providers/swiggy.py` translates normalised tools to Swiggy's and refuses anything outside a read allowlist before it reaches the network. Writes are not implemented in the provider until T6.3.
- Names mapped: `get_menu` to `get_restaurant_menu` (`restaurantId`, `pageSize` max 8), `search_dish` to `search_menu` (`restaurantIdOfAddedItem`, `vegFilter` 0/1), `list_addresses` to `get_addresses` (paged, up to 3 pages), `search_products` to the Instamart server.
- Run limits for real modes are wider (`RunBudget.for_mode`): 300 s, 14 iterations, 45 s per tool call (Spike A: one call took about 16 s). This revises section 10.1's 15 s.
- Dry-run in the app: set `MOODMEALS_MODE=dry_run`, press "Connect to Swiggy" in the sidebar (two sign-ins), then plan as usual. Address text is hidden by default for screen recording.
- Unverified until the owner's check run: real field names inside `addresses[]`, whether Instamart accepts the Food address id, and the real menu shapes through the provider.

### Phase 6, T6.3 implementation notes (2026-10-05)

- Live mode needs `MOODMEALS_MODE=live` and `MOODMEALS_ALLOW_LIVE=1` (never on the public site), and a sidebar box ticked each session. Live changes the cart only (R10.9): after one approved cart update the run ends with `cart_updated`, and the person reviews the real bill and orders in the Swiggy app. Payment tools are never called (R10.7).
- `SwiggyProvider` has two live-only writes, `update_food_cart` and `update_cart`, translated from the plan and sent only after the `WriteGate` approves; its reply is dropped (it can hold the address and the bill). `place_food_order` and `checkout` are not in any allowlist.
- DQ6 is implemented in `Agent`: the cart for the chosen path is read when the plan reaches approval and again just before the write (`_cart_guard`). Status only (`empty`, `not_empty`, `unknown`) goes into the state. `not_empty` needs `confirm_replace` first; `unknown` stops the run (`cart_unverified`). The Instamart cart is read for cook plans and the Food cart for order-in plans, never both.
- The validator rejects live order-in dishes with variants (`variants_unsupported`), R10.11.
- Real shapes: the Instamart cart reply has a top-level `items` list (observed read-only, 2026-10-05). Food's empty-cart reply (observed 2026-10-05) is an envelope: `statusCode` 0, `successful` true, `data` null. A Food cart WITH items has not been seen, so any other shape reads as `unknown` and a Food write fails closed. `scripts/swiggy_check.py` step 5 shows whether both carts are read.
- Tested with a fake connection only (`tests/test_live_guards.py`); no real write has been made.

### Phase 7, T7.1 implementation notes (2026-10-05)

- Scenarios are YAML in `evals/scenarios/` (format and the "appropriate path" rubric in its README); `evals/scenario.py` loads and validates them strictly. Compared with the sketch in 13.1: `world` takes `addresses` and the real switch names; `user_script` adds `address` (index), `constraints` and `mid_run`; `expect.paths_acceptable` is the rubric; `smoke: true` marks the smoke set; `rationale` is required so the owner can review each expectation; runs always stop at the approval screen (`approve` is fixed to false).
- `pyyaml` is now a dependency.

### Order-in fallback and the fourth action (2026-10-05)

- Owner rule (R4.3): when ordering in is not possible, offer a ready-to-eat or quick-cook Instamart meal (a `cook`-path plan whose reason says why), or stop with a reason.
- The model gets a fourth action, `stop_search(reason)` (section 4.2 now lists four: `tool_call`, `ask_user`, `propose_plan`, `stop_search`). The loop accepts it only after at least one tool call (otherwise it is a protocol error), redacts the reason, and ends the run as `STOPPED` with stop reason `no_option` and that sentence as the message.
- Prompt `agent_v4` adds the section WHEN ORDERING IN IS NOT POSSIBLE; v3 stays in `prompts/` for the record. The mock world gained four synthetic ready-to-eat and quick-cook products (appended, so existing ids and seeds are unchanged). `DemoLLM` follows the rule.
- Not yet done: the plan card still says "Cook at home" for a quick-meal fallback plan; the eval scorer must check `blocked_reason` and `quick_meal` with explicit word lists (T7.2).

### Phase 7, T7.2 implementation notes (2026-10-05)

- `evals/run.py`: `run_scripted` is the scripted user (canned answers by question field, address by index, mid-run changes after the plan is shown, never approves) and returns a `RunRecord`. Strategies: `agent` (the loop with any model client), `fixed_workflow` (`evals/fixed_workflow.py`: no model, always order in, best open restaurant by rating, one in-budget item, never looks at Instamart, stops when nothing fits) and `one_shot` (no tools or questions; the ids it names are checked against the whole world, which counts hallucinated entities). `agent_no_validator` is T7.5.
- `evals/scoring.py`: every check is true, false or not applicable: outcome, path (the scenario rubric), hard constraints (the validator's V4, against the NEW values after a mid-run change), hallucinated entities (the validator's V1 codes, or the one-shot count), questions within the limit, address picker asked, trajectory (must call and must not call), no writes without approval (any executed write counts, because evals never approve), stop has a reason, fallback plan (word lists `BLOCKED_WORDS` and `QUICK_WORDS`), failure handled, replanned after a change. Also cost, tokens, latency, tool calls. `summarise` and `k_of_n` give "k of n" per strategy.
- Clear stops are `no_option` and `could_not_verify` with a message of at least 10 characters. Limit stops, protocol errors and model errors are failures.
- Known limits: the scripted demo model never cooks and asks only about diet, so it fails S-02 and never learns a budget that is only available as an answer. Scenarios that expect a budget but state it only as an answer (S-01) are unfair to any agent that does not ask; decide whether to state it up front.

### Phase 7, T7.3 implementation notes (2026-10-07)

- `evals/runner.py` (`python -m evals.runner`, usage in `evals/README.md`) runs scenarios x strategies x runs and writes `evals/results/<date>-<model>.json` (never overwriting: a `-2` suffix is added). A file holds the model, provider, prompt version, git commit and dirty flag, cap, spend, a "k of n" summary and one row per run; failed runs carry a trace.
- Spend cap (E2, default Rs 1,500): token cost from `pricing.py`, converted at `--usd-inr`, checked before every model call by a wrapper client, so the total passes the cap by at most one call. The run in progress when the cap is hit is dropped from the scores but its cost is counted; the eval stops with exit code 3 and the file is still written. A model with no price is refused. The key is read from the environment and never printed or saved.
- Not done: temperature is not set (the adapters use the provider default, and some models reject other values), so the file records "provider default" instead of the 0 to 0.2 in 13.4; "cache target answers for repeat runs" (E2) is not built; `USD_INR` (88.0) and the price table are unverified config.
- First real smoke run (2026-10-07, Groq free tier): every agent run ended in `rate_limited` and was scored as an agent failure, which said nothing about the agent. The runner now paces calls (default 20 s for Groq), waits out rate limits with a growing pause, records unreachable-model runs as `invalid` (not scored, results version 2) and stops after 3 in a row (exit code 4). The earlier results file was removed for the same reason. One real finding is kept: on S-03 the model replied in plain text instead of calling a function (a protocol error).
- Second Groq smoke run (qwen/qwen3.8-27b, 2026-10-07): S-02 passed, S-01 got a valid, grounded order-in plan but failed `must_call get_menu` because the model used `search_dish` on one restaurant (scenario over-specified; now only `search_restaurants` is required), S-06 was invalid (network blip). S-03, S-04 and S-05 ended in `max_seconds`: the 20 s pacing between calls was counted against the 90 s run limit. The runner's client wrapper now tracks `paused_s` and `eval_guard` subtracts it, so waiting for quota never counts as agent time. `--only S-04,S-06` reruns chosen scenarios. The results files of that run were removed from the repo; the S-05 trace still shows a behaviour to check on the next run: after timeouts the model retried restaurant search with three different queries instead of switching to a quick meal.
- Third Groq smoke run (qwen/qwen3.8-27b, prompt v4, 2026-10-07, committed as `evals/results/2026-10-07-qwen-qwen3.8-27b.json`): the agent passed 4 of 6 (S-01, S-02, S-03, S-06; the fixed workflow 3 of 6). Findings:
  - S-04: the model called `stop_search` after two restaurant searches and never tried Instamart quick meals, so no plan and a failed trajectory. Fix: code now refuses `stop_search` until both `search_restaurants` (or a menu lookup) and `search_products` have been tried (`Agent(strict_stop=True)`; the code baseline opts out), and prompt v5 says the same.
  - S-05: ended in `max_seconds` after one failed restaurant search; events carried no latency, so the cause (a slow model call, or the adapter's retry after a 60 s timeout) is not visible. Fix: the first event of every model turn now records that call's latency and tokens, and prompt v5 says that after a failed restaurant search the model should go straight to Instamart instead of retrying other queries.
  - The results file is kept as published (failures included, E1).
- Fourth Groq run (S-04 and S-05 only): S-04 was INVALID with `http 413: Request too large` (Groq's free tier caps one request at roughly 6k tokens). The model view sent every earlier tool result in full on every turn, so a run that searched restaurants and Instamart passed the cap. Fix (also a cost saving for every model, R14): the view keeps only the 3 most recent tool results in full (`RECENT_RESULTS_IN_FULL`, older ones become a one-line note with their count; the ledger still holds every item for the validator), and each result shows at most 8 restaurants, 12 products or 24 menu items. The runner now keeps 300 characters of an invalid run's reason, so a provider's limit numbers are visible.

### Phase 8, T8.1 implementation notes (2026-10-07)

- `data/replays/` holds six recorded mock runs (four successes by the scripted demo model, two failures: the fixed baseline choosing the wrong path, and a real model's failed run read from an eval results file). `scripts/make_replays.py` records them; replays carry `title`, `note` (what to notice) and `tag` (success or failure). `replay_from_trace` builds a replay from a failed run's trace. The Replay page lists runs by title with a mark, shows the note and a plain outcome line.
- Plan card headings: "Order in from Swiggy Food" and "Cook or grab it from Instamart" (a quick-meal fallback is not cooking). A `no_option` stop is a warning, not an error. The trace table shows the model call's latency (`ms`) when recorded.

### Phase 7, T7.5 part 1: the full scenario set (2026-10-07)

- `evals/scenarios/` now holds 24 scenarios in the mix from requirements 7.2 (6 happy path, 4 missing info, 3 contradictions, 3 infeasible, 3 tool failure, 3 mid-run change, 2 safety). The README lists them all.
- New world switch `inject_text` (a prompt-injection test: text hidden in the first dish name, since descriptions are not shown to the model). New expectation fields `assumptions_listed`, `min_items_qty`, `max_items_qty`. New scoring checks `plan_valid` (all validator checks), `assumptions_listed`, `plan_size`; "another idea" now requires a genuinely different plan.
- With the stand-ins (scripted demo and the fixed baseline) all 24 scenarios run and never write. They fail S-02, S-09 and S-17 (never cook or search Instamart) and S-22 (a one-portion plan for four), which is what they should fail. No real-model run of the new scenarios exists yet.
- `agent_no_validator` (2026-10-07): `Agent(validate=False)` skips the plan validator (the validation event carries `validator: off`); `evals/run.py` uses it only for that strategy and the app never does. Plans are still built from the ledger, so an invented id shows up as "unknown item" at Rs 0 and the scorer's own validator pass counts it. Compare it with `agent` on the same model to show what the validator catches. The tests prove the ablation changes outcomes (a closed restaurant, an invented item and a budget-breaking plan get through) and that a valid model is unaffected.
- Runner safety for long runs (2026-10-07): progress is written after every run to `evals/results/<date>-<model>.partial.json` (git-ignored), Ctrl+C returns the results so far (`interrupted: true`, exit code 130), and a crash keeps the partial file. The default exchange rate is Rs 97 per dollar, as given by the owner.
- Full run and what it changed (2026-10-07): results in `evals/results/` (merged file at the top level, raw parts in `parts/`, early attempts in `archive/`; see its README). `scripts/merge_results.py` builds the merged file from parts, drops invalid and stalled runs (reported under `excluded_runs`), checks every scenario has its expected runs, and re-scores `fallback_plan` from stored traces. The runner now marks a run invalid when one model call exceeds 3 minutes (a laptop asleep or a network stall), keeps latency and tokens in failed traces, and records the validator's error codes per run (`metrics.validator_errors`, `slowest_call_ms`). The scorer's blocked-reason word list gained "timed out", "timeout", "isn't possible" and "possible nahi", and normalises typographic apostrophes.
- Owner decisions after the full run (2026-10-07): S-04/S-05 stay strict (no staple sides next to a quick meal), so prompt v6 says to offer only quick-meal items. S-23's limit is 6 items in total (was 2). S-11 exposed a code bug: `state.missing_signals` was never filled, so validator V7 never fired. It now starts as budget, party size and diet, and is reduced by constraints given at the start, by answers and by `change_constraints` (`_note_stated`). V7 still follows R1.5: it requires listed assumptions only once the question budget is spent. For runs that ask fewer questions, prompt v6 asks for assumptions about each entry in `missing_signals` (prompt rule, not a code rule). `PROMPT_VERSION` is `agent_v6`; the full run above used v5, so S-04, S-05 and S-11 are to be re-run on v6.
- Re-run on prompt v6 (2026-10-08): S-04 and S-05 now pass 3 of 3 for `agent` and `agent_no_validator`. S-11 first passed 1 of 3, because an answer with no budget value ("no limit") was counted as a stated budget; a budget or party size now counts only when a value was parsed, and S-11 then passed 3 of 3. Merged result (gpt-5-mini, 24 scenarios): `agent` 71 of 71 checks-passing runs, `agent_no_validator` 67 of 71 (three `plan_valid` failures the validator would have caught, one S-09 outcome miss), `fixed_workflow` 16 of 24. `scripts/merge_results.py` now accepts parts with different prompt versions and labels the file `mixed`, with the scenarios per prompt under `prompt_versions`.

### Phase 7, T7.6: results page (2026-10-08)

- `app/pages/3_Results.py` reads committed files in `evals/results/` (the merged full run first) through `evals/report.py` (pure functions, tested). It shows runs passing every check per strategy as "k of n", a check-by-check table, a scenario-group table, every failed run (none hidden), and cost and speed per run. It notes mixed prompt versions and excluded stalled runs. No network, no key, no model call.
