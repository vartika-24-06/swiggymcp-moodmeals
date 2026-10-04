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

**OpenAI gpt-5-mini (native, 3 repeats per prompt).**

| Run | Valid | Right | Median s |
|---|---|---|---|
| default | 11/15 | 11/15 | 5.0 |
| parallel calls off | 15/15 | 15/15 | 4.3 |

In the default run, the 4 misses were not wrong answers: on the two "find ingredients"
prompts it returned 7 to 10 searches in one turn (parallel tool calling), which breaks
our one-action-per-turn contract. With `parallel_tool_calls` off it was valid and right
on every call. It is also about ten times slower per call than the Groq models, which
matters for the 90-second run budget.

**Gemini: not scored.** `gemini-3.8-flash` returned HTTP 503 (server unavailable) and a
second Flash model stalled on repeated calls, both on 2026-10-04 in the evening. No
valid or right counts were obtained, so nothing is claimed about its tool calling.
Treated as "unreliable at the time of testing"; it can be retried later without any
change to the plan.

**Provisional decision (DQ3).** Build the `LLMClient` on native function calling.
Do not build a JSON fallback yet. Treat the grounding and plan-building step as the
risk to test in the real loop, backed by the validator (it rejects bad plans and
allows 2 retries). Revisit after the Gemini and OpenAI runs.

## Spike C: shape of real responses (T0.4, DQ2), captured 2026-10-04

Shape reports: `spikes/results/shapes_food.json`, `shapes_im.json` (checked: no digit
runs, emails, names, phones or address words). Raw captures stay local and git-ignored.

**What the real shapes changed in our assumptions**
- **Restaurant search:** `availabilityStatus` is "OPEN", "CLOSED" or "UNAVAILABLE", not a
  boolean. 8 of 10 results for "khichdi" carried "(Ad)" in the name, so sponsored
  handling matters in practice. `dishes` was empty. Paging uses `hasMore` and `nextOffset`.
  There is no veg filter on restaurant search. A `veg` boolean appeared on 3 of 10
  (meaning not yet confirmed, so it is not used).
- **Menu:** `isVeg` is a boolean (not a classifier string, and cannot tell egg from
  non-veg); `inStock` is the number 1; categories hold `items[]`, nested ones hold
  `subcategories[]`; `pageSize` max is 8 categories. One menu page was the biggest
  payload on Food.
- **Dish search (`search_menu`):** takes `vegFilter` (1 = veg only) and an optional
  restaurant scope (`restaurantIdOfAddedItem`). Items carry `menu_item_id` but no
  restaurant id, so we must scope the search to one restaurant to know where a dish is
  from. Addons come with ids and prices.
- **Instamart `search_products`:** `vegClassifier` is "VEG_CLASSIFIER_VEG" or
  "VEG_CLASSIFIER_INVALID" (no non-veg seen in this sample); `isPromoted` was true on a
  large share of products; badges include AD, TRENDING and "BUY AGAIN" (history leak, R7.2);
  prices are `offerPrice` and `mrp` numbers; `maxQuantity` and `vegClassifier` are per variant.
  The `addressId` is required.
- **Location:** the tools also accept latitude and longitude (a guest flow), so a
  no-address mode may exist. Not explored.

**Token numbers: not final.** The `est_tokens` in the reports double-count each response
(structured and text copies both counted), so real sizes are about half. Before-and-after
compaction numbers still need an offline run on the local captures.

**Done in code:** parsers for these shapes (`moodmeals/tools/normalise.py`), the
whitelisted model view (`moodmeals/tools/compact.py`), the veg-classifier mapping fixed
for the real strings. Tests use synthetic payloads in the observed shapes.

**Still to do for T0.4:** the offline compaction check on local captures (fix the
double count in `capture_shapes.py` first), then record the token numbers here.
