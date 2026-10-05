# MoodMeals: Tasks

**Status:** Draft v0.1 · **Date:** 2026-10-04
**Implements:** design.md v0.2, requirements.md v0.8
**Who:** **CC** = Claude Code does it; **You** = you do it by hand (needs your account, machine or judgment); **Both** = Claude Code builds, you check.
**Size:** S = under an hour, M = a few hours, L = a day or more of work sessions. These are rough, not promises.

Order matters: spikes first (they can change the plan), then pure code, then the loop, then the model, then the app, then real Swiggy, then evals, then polish. Every task says what "done" means.

---

## Milestones

| Milestone | Meaning | After phase |
|---|---|---|
| **M1** | The agent loop runs end to end on the mock world with a fake model, with all safety tests green | 3 |
| **M2** | A real model drives the loop on the mock world through the Streamlit app, mock-only | 5 |
| **M3** | Dry-run against real read-only Swiggy data works on your machine, and evals are published | 7 |
| **M4** | Demo-ready: replay gallery, video, README, case study, public mock-only deployment | 8 |

## Phase 0: Setup and spikes

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T0.1 | Create the GitHub repo, Python project skeleton (folders from design section 2), pytest, ruff, a basic CI check | Both | S | `pytest` and `ruff` run green on an empty test; repo pushed | design 2 |
| T0.2 ✅ done 2026-10-04 | **Spike A: Python sign-in to Swiggy on localhost.** Small script using the `mcp` SDK, OAuth with phone and OTP, one call to `get_addresses` | You (CC writes the script) | M | Script prints the address count, nothing else. Outcome and any fallback written to `spikes/notes.md` | DQ1, Q2 |
| T0.3 ✅ done 2026-10-04 (Groq and OpenAI scored; Gemini unavailable when tested) | **Spike B: tool-calling reliability.** Tiny harness: 5 canned prompts, 3 fake tools, 3 to 4 candidate models (free tiers first). Measure how often each returns a valid action | Both | M | A table of valid-action rates per model; a recommended-models list; decision on whether a JSON fallback is needed | DQ3, R14 |
| T0.4 ✅ done 2026-10-05 | **Spike C: result compaction.** Save redacted real responses (restaurants, menu, products) as test fixtures, count tokens before and after whitelisting fields | Both | S | Compaction rules chosen with token numbers; redacted fixtures committed (no addresses, phones or names of people) | DQ2, R12 |
| T0.5 | Decisions from the spikes recorded in design.md (update DQ1 to DQ3 as settled) | CC | S | design.md updated | design 14 |

**Gate:** if Spike A fails, the live demo uses another MCP client for sign-in (fallback), and nothing else changes. If Spike B finds no workable free model, the public site is bring-your-own-key plus replay only (already the plan).

## Phase 1: Foundations (pure code, no model)

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T1.1 ✅ done 2026-10-04 | Normalised types and parsing: `Restaurant`, `MenuItem`, `Product`, `Variant`, `Plan`; `parse_count`, `parse_cost_for_two`, `parse_eta`, `map_veg_classifier`, `strip_ad_marker` | CC | M | Unit tests pass, including "5.1K+", "₹400 for two", "10-20 MINS", invalid veg classifier becomes `unverified`, "(Ad)" detection | R5.5, R7.4, R7.7 |
| T1.2 ✅ done 2026-10-04 | `RunState`, `Event` schema, and the redaction function that runs at event creation | CC | M | State round-trips to JSON; redaction tests show no fixture address or phone pattern survives | R12, R13 |
| T1.3 ✅ done 2026-10-04 | `RunBudget`, guardrail checks, cancel flag, question budget | CC | S | Tests for each limit and for the 4th question being refused | R1.2, R11 |
| T1.4 ✅ done 2026-10-04 | `WriteGate`: mode check, approval bound to params hash, single use, expiry, dry-run preview | CC | M | Tests: approve, params mismatch, reuse, expiry, dry-run block, every outcome logged | R10 |
| T1.5 (removed 2026-10-04) | Crisis matcher dropped as out of scope (requirements 12.2). The number is kept so other references stay stable | - | - | - | - |
| T1.6 ✅ done 2026-10-04 | Plan validator V1 to V9 and totals | CC | M | One test per check, including unverified veg, max quantity, price mismatch, closed restaurant, sponsored flag, dry-run disclaimer present | R8, R5.2, R6 (removed), R7, R10.8 |

## Phase 2: Mock world

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T2.1 ✅ done 2026-10-05 | Synthetic fixtures with invented names, matching the observed real shapes (restaurants, menus, products with variants) | CC | M | Fixtures validate against the normalised types; a scan finds no real names or addresses | A4, A5, R12.3 |
| T2.2 ✅ done 2026-10-05 | Failure switches: timeout, empty result, partial menu, out of stock, "(Ad)" entries, invalid veg classifier, quantity limits | CC | M | Each switch has a test showing the intended behaviour | R9, section 16 |
| T2.3 ✅ done 2026-10-05 | `MockProvider` implementing `ActionProvider`, deterministic from a seed | CC | M | Same seed gives the same world; simulated writes live in memory only | design 7.1 |

## Phase 3: The agent loop (fake model)

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T3.1 ✅ done 2026-10-05 | State machine: CHECKIN, GATHER, PROPOSE, VALIDATE, AWAITING_APPROVAL, EXECUTE, DONE, STOPPED; `run_until_pause` generator and `resume` | CC | L | Transition tests; a run pauses at approval and resumes | design 4 |
| T3.2 ✅ done 2026-10-05 | `model_view()` PII firewall and the three action types with protocol-error handling | CC | M | PII tests: no address text, phone or cart contents in any model message | DD4, R12.1 |
| T3.3 ✅ done 2026-10-05 | Address handling: single address uses it and tells the user; several go to a UI picker; never auto-selected | CC | S | Tests for both cases; the question budget counts the picker | R3 |
| T3.4 ✅ done 2026-10-05 | `FakeLLM` and scripted loop tests: happy path, infeasible, tool failure, mid-run change, "another idea", validation retry, limit breach | CC | L | All scenarios pass with no model cost | R4, R8.4, R9, R11 |
| T3.5 ✅ done 2026-10-05 | Prompt injection test: a dish description containing an instruction changes nothing | CC | S | Test passes | design 10.4 |

**M1 reached here (2026-10-05).** Note: the loop tests use a scripted `FakeLLM`; real models arrive in Phase 4.

## Phase 4: Real model

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T4.1 ✅ built 2026-10-05, live smoke test pending | `LLMClient` adapters: OpenAI-compatible and Anthropic; pricing table with a last-checked date; cost estimate | CC | M | A live smoke test on the mock world returns a valid action sequence on at least one free-tier model | R14 |
| T4.2 v1 drafted 2026-10-05, wording review pending | System prompt v1 as a versioned file (SCOPE, ALLOWED, PROHIBITED, GROUNDING, TONE, ESCALATION), including the R4.4 heuristics as guidance | Both | M | Prompt committed with a version; you review the tone and wording of the check-in (Q8) | R1, R4, design 9.4 |
| T4.3 first pass 2026-10-05 (5 of 5 planned, prompt v3 pending rerun) | Run 5 manual scenarios on the mock world with a real model; fix the obvious prompt problems | Both | M | Notes on failures kept (they feed the case study) | design 13 |

## Phase 5: Streamlit app (mock only)

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T5.1 | Plan page: prompt, quick-pick questions, address picker (mock addresses), plan card, "another idea", cancel | CC | L | A full run works in the browser on the mock world | R1, R4, N1 |
| T5.2 | Approval card showing exactly what the action will do; dry-run disclaimer; one action per approval | CC | M | Approve and reject both work; nothing executes without approval | R10 |
| T5.3 | Trace panel with rationale, tool calls, validation, tokens, cost and time | CC | M | Every event type renders; cost shown before and after a run | R13, R14.3 |
| T5.4 | Key input (password field, memory only), pass-through notice, mode check that refuses dry-run and live on the public deployment | CC | S | Test for the startup refusal; notice visible | R12.5, R10.5 |
| T5.5 | About page: limits, disclaimer, privacy, cost, no Swiggy endorsement | Both | S | You approve the wording | R2.3, R15.3 |
| T5.6 | Replay page and "export run" in mock mode | CC | M | A recorded run plays with no model or tool calls | R15.1 |

**M2 reached here.**

## Phase 6: Real Swiggy (your machine only)

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T6.1 | `SwiggyProvider`: MCP client, normalisers built against the redacted fixtures from T0.4, error mapping | CC | L | Normaliser tests pass on fixtures; no real data in the repo | design 7.3 |
| T6.2 | Dry-run mode end to end: real read-only searches and menus, writes previewed only | Both | M | On your machine, a run from check-in to approval screen works on real data, with no cart or order touched | R10.2, D5 |
| T6.3 | Live-mode guard rails: opt-in flag, existing-cart check and warning before an Instamart cart update, approval screen reversibility warning | CC | M | Tests with a fake MCP server: no write without approval; the cart check blocks silent overwrite | R10.6, DQ6 |
| T6.4 | Optional: one live cart update on your machine to confirm the real bill appears (you choose; no order placed) | You | S | Result noted; skip is fine | design 3 |

## Phase 7: Evals

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T7.1 | Scenario format and the first 6 scenarios (smoke set) | Both | M | You review the scenarios and the expected paths (Q5) | R15, E |
| T7.2 | Scripted user, scoring functions, strategies `one_shot`, `fixed_workflow`, `agent` | CC | L | Scoring unit-tested on hand-made runs | 13.2, 13.3 |
| T7.3 | Runner with a spend cap (default ₹1,500), results JSON with model, prompt version and commit | CC | M | A capped run stops at the cap; results file written | E2 |
| T7.4 | Smoke run (6 scenarios, 1 run each), read the failures, fix what is a bug | Both | M | Failures sorted into bugs and genuine limits | 13.4 |
| T7.5 | Remaining scenarios (24 total), `agent_no_validator`, then the full run (3 runs each) if the budget allows | Both | L | Results committed, including failures | R15, E1, E3 |
| T7.6 | Evals dashboard page reading the committed results | CC | M | Charts by strategy; failure gallery; counts shown as "k of n" | R15 |

**M3 reached here.**

## Phase 8: Polish and portfolio

| ID | Task | Who | Size | Done when | Covers |
|---|---|---|---|---|---|
| T8.1 | Replay gallery: at least 6 recorded mock runs, including 2 failures | Both | M | Gallery works with no keys | R15.1 |
| T8.2 | Check the product name and branding (Q4); remove any wording that implies Swiggy endorsement | You | S | Name settled; no Swiggy logos | R15.3 |
| T8.3 | Demo video script, then recording on localhost in dry-run with a test account or full redaction (Q7) | You | M | Video shows no addresses, phones or names | R15.2, R12.4 |
| T8.4 | README: what it is, architecture picture, limits, the Dineout decision, how to run each mode | Both | M | A reader can run mock mode in five minutes | R15.3 |
| T8.5 | Case study outline: problem, the three-question principle, what the agent decides versus code, eval results including failures, what I would do next | Both | M | Draft you are happy with | goal of the project |
| T8.6 | Public deployment, mock only, on a free host | You | S | Link works with no key (replay) and with a key | N1, D6 |
| T8.7 | Final pass against the definition of done in requirements section 11 | Both | S | Every box ticked or an honest note | DoD |

**M4 reached here.**

## Things only you can do (collected)

- Create the GitHub repo and connect Claude Code (T0.1).
- Run Spike A on your machine and report the result (T0.2).
- Approve check-in wording and the prompt tone (T4.2, Q8).
- Run dry-run on your machine (T6.2) and, if you choose, one live cart update (T6.4).
- Review the scenarios and expected paths (T7.1, Q5).
- Name and branding check, video recording, public deployment (T8.2, T8.3, T8.6).

## Cut lines (if time gets short)

Drop in this order, none of which breaks the core story:
1. `agent_no_validator` strategy and the full 3-run eval (keep the smoke set plus one full pass).
2. The Anthropic adapter (keep the OpenAI-compatible one).
3. Live mode (keep dry-run).
4. Scenarios from 24 down to 16.

**Do not cut:** the validator, `WriteGate`, the PII firewall, the trace, and the baseline comparison. They are the point of the project.

## Risks to watch

- **Spike A fails** (sign-in): the live demo needs another client. Plan unaffected otherwise.
- **Free models call tools badly:** the demo leans on the replay gallery and bring-your-own-key.
- **Real Swiggy response shapes change or differ from the fixtures:** the normalisers are tested against saved fixtures and fail loudly instead of guessing.
- **Streamlit reruns cause state bugs in the approval step:** covered by the resumable design and tested in T3.1 and T5.2.
- **Scope creep:** the cut lines above are the answer.
