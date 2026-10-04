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
Exactly one of three action types (structured, validated by code):
1. `tool_call(name, params)`: a read tool from the catalogue for the current state.
2. `ask_user(question, options?)`: refused by code once the question budget is spent.
3. `propose_plan(path, reason, items[], assumptions[])`: moves to VALIDATE.

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
| `update_cart(items)` | Instamart `update_cart` | amber | Replaces the whole cart; code merges safely. |
| `place_food_order()` | Food `place_food_order` | red | Live only, per approval. |
| `checkout()` | Instamart `checkout` | red | Live only, per approval. |

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
- Two adapters *(proposed)*: an OpenAI-compatible one (OpenAI, Groq, OpenRouter, Gemini through its compatible endpoint) and an Anthropic one. Each converts the model's native tool-calling into the three action types of section 4.2.
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
- "Another idea" re-enters GATHER with the previous plan marked as rejected (R4.2). An infeasible path (nothing open, nothing in stock) triggers a switch with a one-sentence explanation (R4.3, R9).

### 9.4 Prompt as a specification
The system prompt is a versioned file (`prompts/agent_vN.md`) with fixed sections, so it can be tested and diffed: **SCOPE**, **ALLOWED ACTIONS** (the three action types), **PROHIBITED ACTIONS**, **GROUNDING RULES** (only use entities from tool results), **TONE** (short, no medical talk), **ESCALATION** (when to stop and say it can't produce a verified plan). The prompt version is stored with every run and eval result.

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
| DQ2 | **Result compaction:** how much of each tool result the model sees (top N restaurants, menu caps, whitelisted fields) to keep context small and cheap | Before the loop is built |
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
| DD3 | The model outputs one of three action types per turn | Proposed |
| DD4 | **PII firewall:** the model never sees addresses, phones, other people's names, order history or cart contents | Locked |
| DD5 | `WriteGate` approvals bind to one exact action (params hash); no automatic retries of writes | Proposed |
| DD6 | Dry-run shows an item total only, with a disclaimer that the final bill is shown only in live mode | Locked |
| DD7 | OAuth tokens and model keys are memory-only; the owner re-signs in at each launch | Locked |
| DD8 | Evals use a scripted user and deterministic scoring; no model judge in v1 | Proposed |
| DD9 | One action per turn: the model client asks providers for serial tool calls (`parallel_tool_calls` off, Anthropic `disable_parallel_tool_use`). Reason (Spike B): gpt-5-mini batched 7 to 10 searches in one turn, which breaks the one-action contract and would burn the 20-call budget. With it off, gpt-5-mini was valid and right on 15 of 15. Providers that cannot turn it off (Gemini): the loop must still handle several calls, by rejecting the turn with one corrective message (design 4.2) | Proposed |

## 16. Error handling

| Situation | Response | Req |
|---|---|---|
| Tool timeout or error (read) | Retry once, then alternative or switch path; trace both | R9.1 |
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
