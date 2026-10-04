# Spike notes

Outcomes of the Phase 0 spikes (tasks.md). No personal data belongs here: no
addresses, phone numbers, names or counts taken from an account.

## Spike A: Swiggy sign-in from Python (T0.2, design DQ1, requirements Q2)

- **Date:** 2026-10-04
- **Result:** SUCCESS
- **Environment:** owner's Windows laptop, `mcp` SDK 2.3.0 on Python 3.14.7
- **Script:** `spikes/swiggy_signin.py`

### What worked

- **Sign-in:** OAuth with dynamic client registration and PKCE, as a public
  client, with the redirect to `http://localhost:8765/callback`.
- **`list_tools`:** returned 20 tools: `get_addresses`, `create_address`,
  `delete_address`, `search_restaurants`, `search_menu`, `get_restaurant_menu`,
  `get_food_cart`, `update_food_cart`, `flush_food_cart`, `place_food_order`,
  `fetch_food_coupons`, `apply_food_coupon`, `get_food_orders`,
  `get_food_order_details`, `track_food_order`, `get_food_delivery_status`,
  `report_error`, `get_payment_options`, `check_payment_status`, `confirm_order`.
- **`get_addresses`:** returned in about 16 seconds, and `pagination.total` was
  readable in the result. It was the only tool called.

### Fallback

Not needed. The gate in tasks.md (another MCP client for sign-in) does not apply.

### Open points

- **Unexplained stall:** the first run signed in and then showed no output for
  over a minute at the `get_addresses` call. Progress lines and a 90-second
  limit were added afterwards and the next run finished normally. The cause is
  unknown.
- **Tool timeout:** about 16 seconds for one call is longer than the 15-second
  `tool_timeout_s` in the run budget (design.md section 10.1). That value needs
  revisiting before the real provider is built.

## Spike B: tool-calling reliability (T0.3, DQ3), first results 2026-10-04

**Setup.** `spikes/tool_calling.py`, 5 synthetic prompts x 3 repeats per model, two
modes: `native` (provider function calling) and `json` (plain JSON reply). Run on
Groq's free tier. **valid** = exactly one known action with schema-correct arguments.
**right** = valid and the sensible action for that prompt.

| Model (Groq) | Mode | Valid | Right | Median s |
|---|---|---|---|---|
| openai/gpt-oss-20b | native | 15/15 | 11/15 | 0.6 |
| openai/gpt-oss-20b | json | 10/15 | 5/15 | 0.6 |
| qwen/qwen3.8-27b | native | 15/15 | 13/15 | 0.3 |
| qwen/qwen3.8-27b | json | 12/14 | 10/14 | 0.2 |

One qwen JSON call failed with a network error (DNS), so it is counted as an error,
not a model miss.

**What this shows (with the limits of 15 calls per cell).**
- Native function calling gave a schema-valid single action on every call for both
  models. JSON-in-text was clearly worse on both, so it is **not a better fallback**
  than native calling for these models. gpt-oss-20b also tried to call a tool when
  none was offered (Groq rejected it with a 400); those count as misses.
- "Right" was lower than "valid". The biggest gap is the prompt that gives search
  results and asks for the next step (P5): gpt-oss-20b native got 0/3 and qwen 2/3
  right. Part of that is the prompt: "decide the next step" can legitimately mean
  another tool call, so P5 is a weak measure. Re-measure it inside the real loop,
  where the phase (propose now) is explicit, before drawing a conclusion.
- Counts this small cannot separate the two models. They show native calling is
  workable on small free models, not that either is reliable.

**Not yet run:** Gemini (`gemini-3.8-flash`) and OpenAI (`gpt-5-mini`).

**Provisional decision (DQ3).** Build the `LLMClient` on native function calling.
Do not build a JSON fallback yet. Treat the grounding and plan-building step as the
risk to test in the real loop, backed by the validator (it rejects bad plans and
allows 2 retries). Revisit after the Gemini and OpenAI runs.
