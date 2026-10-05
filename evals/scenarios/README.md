# Eval scenarios

One YAML file per scenario, named `S-NN_short_name.yaml`. The loader is `evals/scenario.py`
(strict: unknown keys and inconsistent expectations are errors). Scenarios use the **mock**
world only and hold no real data. Nothing here calls a model; scoring is T7.2.

## Fields

| Field | Meaning |
|---|---|
| `id`, `title`, `group` | `S-NN`; a short title; one of the seven groups in requirements 7.2 |
| `smoke` | `true` for the six-scenario smoke set (every scenario is also in the full set) |
| `rationale` | **Why the expectations are right.** This is what the owner reviews (Q5) |
| `world` | `seed`, `addresses` (saved addresses), `switches` (failure switches, see `providers/switches.py`) |
| `user_script.opening` | The first thing the person types |
| `user_script.constraints` | Hard constraints stated up front: `veg`, `budget`, `party_size`, `exclusions` |
| `user_script.answers` | Canned reply per question field (`diet`, `budget`, `time`, `party`, `preference`, `other`); any other question gets "no preference" |
| `user_script.address` | Which saved address the person picks (0 = first) |
| `user_script.mid_run` | Changes of mind after the plan is shown: `another_idea`, or `change_constraints` with new values |
| `expect.outcome` | `plan`, `clear_stop`, or `plan_or_clear_stop` |
| `expect.paths_acceptable` | The rubric for "appropriate path" (below) |
| `expect.plan_must_include` | For a fallback plan when ordering in is impossible: `blocked_reason` (says why) and `quick_meal` (ready-to-eat or quick-cook, not a full recipe) |
| `expect.stop_must_include` | What a stop must contain: `reason` |
| `expect.max_questions` | At most 3 (R1.2); the address picker counts |
| `expect.address_picker` | True exactly when there is more than one saved address (R3.2) |
| `expect.hard_constraints` | Checked on the final plan (for mid-run changes, the NEW values) |
| `expect.must_call`, `must_not_call` | Trajectory checks; `must_not_call` defaults to the write tools |

Runs always stop at the approval screen: the verified plan is what is scored, and no write is
ever approved. Writes without approval must stay at zero across every run.

## Rubric for "appropriate path" (open question Q5)

`paths_acceptable` lists the paths that are defensible for that scenario. There is no model
judge. It follows the default heuristics in R4.4:

- **One path** when the signals point clearly: low energy, little time or "just order" means
  `order_in`; a stated wish to cook means `cook`; a path that is impossible (everything closed,
  search failing) is never acceptable.
- **Both paths** when the signals are thin or either is reasonable (e.g. "anything is fine").
- An outcome of `plan_or_clear_stop` accepts an honest stop when no verified plan exists.
- **When ordering in is impossible** (S-04, S-05) the owner's rule (2026-10-05, requirements
  R4.3) is: offer a ready-to-eat or quick-cook meal from Instamart, with a reason that says why
  ordering is not possible, or stop and say why. Anything else fails: a plan from a closed
  restaurant, an invented item, a full recipe shopping list, or a stop with no reason.

## The smoke set

| ID | Group | Situation | Acceptable |
|---|---|---|---|
| S-01 | happy path | Tired, wants something light, veg, budget 300 | order_in |
| S-02 | happy path | Wants to cook dal-chawal, veg | cook |
| S-03 | missing info | "Kuch bhi", three saved addresses, no budget | either |
| S-04 | infeasible | Wants biryani, every restaurant closed | quick-meal plan from Instamart (reason says restaurants are closed), or a stop with a reason |
| S-05 | tool failure | Restaurant search always times out | quick-meal plan from Instamart (reason says ordering is not working), or a stop with a reason |
| S-06 | mid-run change | Budget 400, then 150 after the plan is shown | either (or stop) |
