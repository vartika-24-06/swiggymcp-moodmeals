# MoodMeals: Requirements

**Status:** Draft v0.8 · **Date:** 2026-10-04
**Owner:** Vartika · **Build:** Python + Streamlit, hand-written agent loop, Claude Code + GitHub, spec-driven
**Companion files (to come):** design.md, tasks.md, evals/scenarios.md

Everything marked *(proposed)* is a starting number to recalibrate after the first eval run. Nothing here is a promise about Swiggy's access or behavior beyond what is stated in section 9.

---

## 1. Overview

People with too many options end up doing nothing. MoodMeals is a "kya khana hai, batao" agent. It takes a vague "I don't know what to eat" and turns it into one concrete, ready-to-approve plan: **cook at home** (Swiggy Instamart) or **order in** (Swiggy Food). It asks at most three questions, recommends one plan, and stops at an approval gate before anything with real-world effect.

It is a portfolio project. Its purpose is to show an agent that decides between genuinely different paths, uses real tools, verifies what it says, handles failure, and is honest about its limits.

### 1.1 Design principles
1. **Decide, don't interrogate.** At most 3 questions, then one recommended plan with an "another idea" option.
2. **Nothing happens without approval.** Dry-run is the default. Live writes are opt-in, one action at a time, on the owner's machine only.
3. **Grounded.** Every restaurant, dish, price and product in a plan comes from a tool result in the same run.
4. **No ongoing cost to the owner.** Users bring their own key, or browse the replay gallery.
5. **Private by default.** Account data (addresses, phone numbers, orders) is never stored, logged or shown in public artifacts.
6. **Decisions visible.** The trace shows what the agent considered and why.

### 1.2 Architecture in one paragraph
A Python agent loop (no framework) calls tools through an **action-provider interface**. The loop lives in a UI-independent package; Streamlit pages (plan, trace, replay gallery, eval dashboard) sit on top of it. Three provider modes exist: **mock** (public deployment, replay, evals), **dry-run** (real read-only Swiggy data, writes stop at approval), and **live** (opt-in writes). Dry-run and live run only when the owner starts the app locally (`streamlit run`), because Swiggy sign-in only allows localhost redirects; the public deployment runs mock only. A deterministic **plan validator** checks every plan against tool outputs before it is shown.

## 2. Scope

**In v1**
- Two paths: Order in (Swiggy Food) and Cook (Swiggy Instamart).
- Light, non-clinical "mood" check-in (craving, energy, willingness to cook, time, budget, dietary needs).
- One recommended plan, an "another idea" action, and an approval gate.
- Mock provider, dry-run provider, live provider (owner-only).
- Run trace, guardrails, plan validator, eval harness, replay gallery, demo video.
- Bring-your-own-key model providers (no hosted key).

**Out of v1**
- Going out to eat (Swiggy Dineout; see D12), a Local in-browser model, Swiggy Scenes, other providers (Zomato, Zepto, etc.), payments beyond what Swiggy's tools already offer, accounts, hosted API key, medical or wellbeing assessment, non-English UI, multi-day trips, group ordering, order modification after placement.

## 3. Glossary

| Term | Meaning |
|---|---|
| Path | One of: Order in, Cook |
| Plan | A single recommended action set for one path: dishes or products, total, timing |
| Action provider | Interface the agent calls for tools; has mock, dry-run, live implementations |
| Dry-run | Real read-only data; write steps are shown but not executed |
| Approval gate | Screen where the user approves or rejects one specific action |
| Plan validator | Deterministic code that checks a plan against tool outputs |
| Grounded | Present in a tool result from the current run |
| Baseline | A simpler strategy used for comparison in evals |
| Replay | A recorded run shown without calling any model or tool |
| Sponsored | A result marked "(Ad)" by Swiggy |

## 4. Users and scenarios

- **S1 Overloaded evening.** "No idea, just tired." Agent asks 2 questions, recommends a path, shows a plan.
- **S2 Specific constraint.** "Veg, under ₹400, two people." Agent asks nothing it already knows.
- **S3 Failure.** Chosen restaurant closes or a product is out of stock. Agent replans within budget.
- **S4 Changed mind.** "Not that, something else." Agent offers a different path or option without restarting.
- **S5 Reviewer.** A recruiter watches the demo video or the replay gallery and reads the trace.

## 5. Functional requirements

### R1 Check-in (max 3 questions)
- R1.1 WHEN a session starts, THE SYSTEM SHALL show a short disclaimer and a free-text or quick-pick prompt for the user's situation.
- R1.2 THE SYSTEM SHALL ask at most 3 clarifying questions in total, including address confirmation.
- R1.3 THE SYSTEM SHALL NOT ask for information already given or available from tools.
- R1.4 Question priority *(proposed)*: address (required, R3.2) > hard constraints (diet, budget) > time window > party size > preferences.
- R1.5 WHEN required information is still missing after 3 questions, THE SYSTEM SHALL state its assumptions in the plan.
- R1.6 Signals gathered are: craving or cuisine, energy, willingness to cook, time available, budget, dietary needs, party size. Moods are not scored, labeled or diagnosed.

### R2 Safety and tone
- R2.1 THE SYSTEM SHALL NOT diagnose, give medical advice or describe the user's mood as a condition.
- R2.2 WHEN free text contains crisis language, THE SYSTEM SHALL stop planning and show the crisis message, using a deterministic check independent of the model (resources: open item Q6).
- R2.3 THE SYSTEM SHALL show a short "planning aid, not advice" line at the start and on each plan.

### R3 Location and account
- R3.1 THE SYSTEM SHALL use the address the user confirms; it SHALL NOT auto-select a saved address.
- R3.2 WHEN multiple saved addresses exist, THE SYSTEM SHALL ask the user to pick one (counts toward R1.2). Swiggy marks its ranked default as a suggestion only.
- R3.3 THE SYSTEM SHALL treat all address and phone data as sensitive (see R12).

### R4 Path decision
- R4.1 THE SYSTEM SHALL choose between Order in and Cook using the check-in signals and tool results, and SHALL show a one-sentence reason.
- R4.2 THE SYSTEM SHALL support "another idea", which proposes a different path or a different option within the path.
- R4.3 WHEN a path is infeasible (nothing open, no stock), THE SYSTEM SHALL say so and move to another path rather than force a plan.
- R4.4 Default heuristics *(proposed, to be tested by evals)*: low energy, low willingness to cook or little time lean to Order in; enough energy and time, or a stated "want to cook" or recipe intent, lean to Cook; a simple few-ingredient meal is preferred when energy is low but the user still wants to cook.

### R5 Order-in path (Swiggy Food tools)
- R5.1 THE SYSTEM SHALL search restaurants for the confirmed address and consider only those with status OPEN.
- R5.2 THE SYSTEM SHALL treat "(Ad)" results as sponsored: it SHALL mark them as sponsored and SHALL NOT rank them above organic results on position alone.
- R5.3 THE SYSTEM SHALL verify that results match the intent (search relevance is loose) before recommending.
- R5.4 THE SYSTEM SHALL read the menu, respect veg and budget constraints, and build a plan with item names, prices and a total.
- R5.5 THE SYSTEM SHALL parse text-formatted values (ratings counts, cost for two) in code, not by model guess.
- R5.6 THE SYSTEM SHALL show the delivery estimate returned by the tool and SHALL NOT invent one.

### R6 (removed)
The go-out path (Swiggy Dineout) was dropped from v1. The number is kept so other references stay stable. See D12 and section 12.

### R7 Cook path (Instamart tools)
- R7.1 THE SYSTEM SHALL propose a simple meal and a product list found through product search, with pack sizes and a total.
- R7.2 THE SYSTEM SHALL use "your go-to items" only if the user permits (it reveals order history; see R12). Search results can also carry "buy again" badges that reveal past purchases; these SHALL NOT be shown in shared traces.
- R7.3 THE SYSTEM SHALL note that cart updates replace the entire cart, and SHALL handle that safely.
- R7.4 THE SYSTEM SHALL treat a product whose veg classification is unknown or invalid as **unverified**, never as veg. WHEN the user has a veg constraint, unverified products SHALL be excluded or clearly flagged, and a product's name or description SHALL NOT be used to override the classifier.
- R7.5 THE SYSTEM SHALL respect each variant's maximum quantity per order and SHALL only offer pack sizes returned by the tool.
- R7.6 THE SYSTEM SHALL ignore "similar products" that do not match the plan, and SHALL mark promoted products as sponsored (as in R5.2).
- R7.7 THE SYSTEM SHALL compute unit price and totals in code from the returned offer price, and SHALL NOT trust text-formatted unit prices.

### R8 Plan validator (deterministic)
- R8.1 EVERY entity in a plan (restaurant, dish, product) SHALL appear in a tool result from the current run.
- R8.2 Prices and totals SHALL be recomputed in code and SHALL match tool values within tolerance 0.
- R8.3 THE SYSTEM SHALL reject plans that break hard constraints (budget, veg, party size, closed restaurant, out of stock).
- R8.4 WHEN validation fails, THE SYSTEM SHALL retry up to 2 times, then show "I could not produce a verified plan" with the reason.

### R9 Replanning and failure
- R9.1 WHEN a tool errors or returns nothing, THE SYSTEM SHALL retry once, then try an alternative (other restaurant, other path).
- R9.2 WHEN the user changes constraints mid-run, THE SYSTEM SHALL update the plan without restarting the check-in.
- R9.3 THE SYSTEM SHALL tell the user what changed and why, in one sentence.

### R10 Approval gate and action modes
- R10.1 THE SYSTEM SHALL NOT call any write tool (cart update, place order, checkout, address changes) without an explicit approval for that specific action.
- R10.2 Dry-run is the default mode. Live writes require the owner to enable them per session on localhost.
- R10.3 THE SYSTEM SHALL show exactly what the action will do (items, total, address label, payment method) before approval, and SHALL NOT bundle several writes under one approval.
- R10.4 THE SYSTEM SHALL record every approval, rejection and write in the trace.
- R10.5 The public site SHALL only run mock mode.
- R10.6 Orders made through Swiggy's tools may not be reversible through the tools themselves (Swiggy's README says food orders cannot be cancelled). The approval screen SHALL say so in live mode.
- R10.7 In v1, THE SYSTEM SHALL NOT call any payment tool (payment options, payment status, confirm order).
- R10.8 In dry-run, THE SYSTEM SHALL show the item total only and SHALL display on every plan: "Item total only. Delivery fees, taxes and discounts are not shown in dry-run. The final bill is shown only in live mode, before you place an order." It SHALL NOT estimate fees or taxes.

### R11 Run guardrails *(proposed)*
- R11.1 Max 12 loop iterations, 20 tool calls, 90 seconds per run, with a visible cancel.
- R11.2 Exceeding a limit SHALL stop the run cleanly with the best verified plan so far or an explanation.
- R11.3 Only structured parameters are sent to tools; free user text is never passed through to a tool.

### R12 Privacy and data handling
- R12.1 THE SYSTEM SHALL NOT store or log addresses, phone numbers, order history or names of people other than the user.
- R12.2 Address IDs are kept in memory for the session only.
- R12.3 Traces for sharing (replay, screenshots, video) SHALL use mock data or redaction.
- R12.4 The README and demo SHALL state that live mode signs in with the user's own Swiggy account, and the owner SHALL use a test account or redaction when recording.
- R12.5 Model API keys are held in the app session's memory only, never written to disk, logs or traces, and never sent anywhere except the chosen model provider. On the public deployment the key passes through the app's server, and the UI SHALL say so before the user pastes it.

### R13 Trace
- R13.1 THE SYSTEM SHALL show each step: what the agent considered, which tool it called, with what structured parameters, and the result summary.
- R13.2 THE SYSTEM SHALL show per-run cost estimate (bring-your-own-key), tokens, tool calls and elapsed time.

### R14 Model providers
- R14.1 THE SYSTEM SHALL use a provider-agnostic model interface.
- R14.2 Menu: bring-your-own-key providers (OpenAI, Gemini, Groq, OpenRouter, Anthropic) with a model dropdown. There is no Local model tier in v1.
- R14.3 THE SYSTEM SHALL show an estimated cost per run before the user starts.
- R14.4 No hosted key, no cost to the owner.

### R15 Replay gallery and demo
- R15.1 A gallery of recorded runs (mock data), including successes and failures, playable with no model or tool calls.
- R15.2 A demo video recorded on localhost showing dry-run against real Swiggy read-only data, with personal data hidden.
- R15.3 THE SYSTEM SHALL NOT claim Swiggy approval, partnership or endorsement anywhere.

## 6. Non-functional requirements
- N1 Public site works with no key (replay) and no sign-in.
- N2 First plan within 90 seconds on a typical key *(proposed)*.
- N3 Accessible basics: keyboard use, readable contrast, no color-only meaning.
- N4 Currency and location default to INR and India, since Swiggy is India-only.
- N5 Failure messages say what happened and what the user can do.

## 7. Evaluation

Evals run against the **mock provider** with seeded scenarios and score **trajectories** (which tools, what order, what parameters) as well as outcomes.

### 7.1 Strategies compared
1. **One-shot baseline:** model answers with no tools (shows the value of grounding).
2. **Fixed workflow:** always Order in, fixed steps (shows the value of path choice).
3. **Agent:** the full loop.
4. (Optional) **Agent without validator**, to show what the validator catches.

### 7.2 Scenarios *(proposed: 24 scenarios × 3 runs)*
| Group | Count | Examples |
|---|---|---|
| Happy path | 6 | Tired and hungry; want-to-cook mood; budget meal for two |
| Missing info | 4 | No address chosen; no budget; ambiguous time |
| Contradictions | 3 | "Cheap" but "premium"; veg but "butter chicken" |
| Infeasible | 3 | Everything closed; key product out of stock; nothing within budget |
| Tool failure | 3 | Timeout; empty search; partial menu |
| Mid-run change | 3 | Switch path; lower budget; add a guest |
| Safety | 2 | Crisis wording; attempt to place order without approval |

### 7.3 Metrics and targets *(all proposed, recalibrate after the first run)*
| Metric | Target |
|---|---|
| Hard-constraint satisfaction | ≥ 90% |
| Hallucinated entities in accepted plans | 0 |
| Questions asked ≤ 3 | 100% |
| Writes without approval | 0 (must be 100% safe) |
| Appropriate path chosen (rubric) | ≥ 80% |
| Failure handled (replanned or clear stop) | ≥ 90% |
| Cost and latency per run | reported, no target |

### 7.4 Rules
- E1 Results are published including failures and limits.
- E2 Eval spend has a hard cap, default ₹1,500; free tiers first; target answers cached for repeat runs.
- E3 Sample sizes are small; calibration-style claims are not made.

## 8. Decisions

| ID | Decision | Status |
|---|---|---|
| D1 | Project is MoodMeals, a "kya khana hai, batao" agent using Swiggy Food and Instamart (Dineout and Scenes out; see D12) | Locked |
| D2 | "Mood" is light and non-clinical; no bands, no scores | Locked |
| D3 | Max 3 questions then one recommended plan | Locked |
| D4 | Python + Streamlit, hand-written loop, no agent framework, UI-independent core package | Locked |
| D5 | Dry-run default; three provider modes; writes gated and opt-in | Locked |
| D6 | Dry-run and live Swiggy modes run only when the owner starts Streamlit locally; the public deployment is mock + replay | Locked |
| D7 | Bring-your-own-key; no hosted key; owner pays nothing ongoing | Locked |
| D8 | Scenario evals with trajectory scoring against mock | Proposed |
| D9 | Reverse-engineer-an-AI-product idea is parked for later | Locked |
| D10 | No Local in-browser model tier in v1 (the public deployment uses bring-your-own-key + replay) | Locked |
| D11 | This is a portfolio project only: no application to Swiggy, no sandbox request. The demo runs in dry-run on real read-only data plus mock data; real orders are never part of the demo | Locked |
| D12 | Dineout (go-out path) is dropped from v1. Reasons in section 12 | Locked |

## 9. Assumptions and verified facts

- **A1 (verified in session, 2026-10-04):** Swiggy's Builders Club docs say local development on localhost needs no approval. Production access needs an application with a localhost demo video; staging with seeded data is issued during review; production follows 48 hours of working staging. Applications are individually reviewed and invite-based in v1. Approval looks for "a concrete use case with real end users", so a portfolio project may not receive production access.
- **A2 (verified):** Sign-in is OAuth with phone and OTP. Swiggy offers Food, Instamart, Dineout and Scenes servers; v1 uses Food and Instamart.
- **A3 (not verified):** No documented fake "local dev stub" was found. Local development appears to call live servers with the developer's own account. Swiggy's GitHub README describes real orders as cash on delivery and not cancellable, while the live tool descriptions also include UPI payment steps; v1 treats any order as real and never calls a payment tool. The README also says third-party app development is "not permitted at this time", which conflicts with the Builders Club docs; the Builders Club pages are treated as more specific.
- **A4 (observed on a real account, read-only):**
  - `get_addresses` returns ids, address text, masked phone, category and tag, 10 per page, with a resolution block that says the default is only a suggestion.
  - `search_restaurants` returns id, name, cuisines, rating, rating count as text, cost for two as text, area, distance, delivery time, availability status, 10 per page with an offset. Some names include "(Ad)". Relevance to the query is loose. It returns no dishes.
  - `get_restaurant_menu` returns restaurant info plus categories of items (name, price, veg flag, rating, in-stock, variants and add-ons flags); full customization needs `search_menu`.
- **A5 (observed read-only on a real account, Instamart):** `search_products` returns products with variants (pack sizes), each with MRP, offer price, a text unit price, rating and rating count as text, delivery minutes, a veg classifier, a per-order maximum quantity, a promoted flag and badges (for example "buy again", which reveals order history). Many plainly vegetarian items carry an **invalid** veg classifier, so the classifier cannot be assumed complete. The response also includes unrelated "similar products".
- **A6 (Dineout, researched and dropped):** see section 12 for the evidence and the reasons.
- **A7 (docs, not observed):** Instamart cart updates replace the whole cart and `checkout` places the order.

## 10. Open questions

- **Q2.** Can the Python MCP client complete Swiggy's OAuth sign-in on localhost? Run an early spike on the owner's machine. If not, live mode would rely on another MCP client for sign-in and the demo video would use it; the public deployment is unaffected.
- **Q4.** Name and branding: check "MoodMeals" availability; use no Swiggy logos or wording that implies endorsement.
- **Q5.** Final scenario list and the rubric for "appropriate path".
- **Q6.** Crisis resources text and regions.
- **Q7.** Demo account: a test account or a redaction process for recording.
- **Q8.** Sign-off on the check-in wording and the heuristics in R4.4.

## 11. Definition of done (v1)
- R1–R5 and R7–R15 implemented and traceable to tests or eval scenarios.
- All writes proven gated by tests (no write without an approval record).
- Evals run for strategies 1–3 with published results, including failures.
- Replay gallery with at least 6 runs (including 2 failures).
- Demo video recorded with personal data hidden; README and case study state the limits, A3 and the no-endorsement rule.
- Eval spend within the cap.

## 12. Scope change log

### 12.1 Dineout (go-out path) dropped, 2026-10-04 (D12)
**What changed.** v1 was planned with three paths (Order in, Go out, Cook). It now has two: Order in (Swiggy Food) and Cook (Swiggy Instamart). Requirement R6 is removed and the product is framed as "kya khana hai, batao".

**Why.**
1. **Reported defects in Dineout's tools.** Public issues on Swiggy's own manifest repository, all open as of 6 September 2026 with no Swiggy reply shown: #104 (the documented cancel tool is not served, so a booking can be made but not cancelled through the tools), #105 (the slots tool leaves the slot and item ids out of the structured result, so they exist only in prose text and clients must parse sentences), #102 (the docs don't mention the structured result at all). Food and Instamart return structured data properly.
2. **We could not observe Dineout working.** Four read-only searches (three from a saved address, one from coordinates given in the tool's own documentation) all returned an empty result with no error. Cause unknown; one unconfirmed possibility is the same missing structured result.
3. **Its write step is the riskiest.** A free booking is created and confirmed in one step and cannot be undone through the tools. That conflicts with the "nothing happens without approval, and approvals should be reversible or clearly warned" principle more sharply than a food or grocery order.
4. **We would be building on guesses.** Without a working observation the Dineout mock and its evals would rest on tool descriptions alone, and the booking path would depend on parsing prose, a fragile step that adds risk without adding to what the portfolio needs to show.

**What stays true about the project.** Two genuinely different paths remain (effort and convenience: cook versus order in), each with real tools, real data shapes we observed, and a decision the agent has to make and explain. The agent behaviours we care about (grounding, validation, replanning, approval gate, trace, evals) do not depend on a third path.

**What we lose.** The social "go out for a meal" scenario. That is acceptable for v1; the meal-decision story is complete without it.

**Revisit when.** Swiggy closes issues #102, #104 and #105 (a structured slots result and a working cancel tool) and a Dineout search returns data from a connected client. The action-provider interface means a Dineout provider can be added later without changing the agent loop.

**Research notes retained from the drop.** Dineout facts from the live tool descriptions: one slots call returns up to 7 days of slots; free deals book in one step and paid deals need UPI; searches take a single term, not a sentence. These are not observed and not relied on.
