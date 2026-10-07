# Evals

- `scenarios/`: the scenario files and their format (see its README).
- `scenario.py`: loads and validates scenarios. `run.py`: the scripted user and the strategies.
  `scoring.py`: deterministic checks. `runner.py`: runs a set and writes the results.
- `results/`: committed results, one file per run of the runner (`<date>-<model>.json`).

## Running

Everything runs on the synthetic mock world. The key is read from an environment variable and
is never printed or saved (`OPENAI_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY`,
`ANTHROPIC_API_KEY`).

```powershell
# Free: the scripted demo model and the fixed baseline
python -m evals.runner --set smoke --strategies agent,fixed_workflow

# See the plan and the rough cost first, run nothing
python -m evals.runner --set smoke --provider groq --model openai/gpt-oss-20b --dry-plan

# A real model (set the key in this terminal first)
$env:GROQ_API_KEY = "..."
python -m evals.runner --set smoke --strategies agent,fixed_workflow --provider groq --model openai/gpt-oss-20b --runs 1
```

## Rate limits and invalid runs

Free tiers limit tokens per minute. Calls are paced (`--min-interval-s`, default 20 s for Groq,
0 for others) and a rate-limit error is waited out and retried (`--rate-limit-retries`,
`--rate-limit-wait-s`). A run where the model could not be reached or answered (rate limit,
bad key, network) is recorded as `invalid` with a reason and is NOT scored: it says nothing
about the agent. Waiting for the quota does not count against a run's 90 s time limit. After 3 invalid runs in a row the eval stops as unreachable (exit code 4) and
still writes its results. Exit codes: 0 ok, 3 spend cap, 4 model unreachable, 130 interrupted.

**Progress is saved as it goes** to `evals/results/<date>-<model>.partial.json` after every run. Ctrl+C ends the eval cleanly and writes the final file with `interrupted: true`; a crash or a sleeping laptop leaves the partial file with every finished run. Do not commit `.partial.json` files (they are git-ignored). The default exchange rate is Rs 97 per dollar (`--usd-inr` overrides it).

Options: `--only S-04,S-06` (just those scenarios), `--set smoke|full`, `--strategies` (comma list of `one_shot`, `fixed_workflow`,
`agent`, `agent_no_validator`), `--runs N` (model strategies only; the fixed workflow is deterministic and runs
once), `--cap-inr` (default 1500), `--usd-inr`, `--out`, `--dry-plan`.

## The spend cap

Spend is estimated from token counts and `moodmeals/models/pricing.py`, converted with
`--usd-inr` (a config value, not a fact: check it), and checked before every model call, so the
total can pass the cap by at most one call. A model with no price in the table is refused,
because the cap could not be enforced. When the cap is hit the run in progress is dropped from
the scores, the eval stops with exit code 3, and the results so far are still written.
Free-tier models are priced at 0 and never trip the cap; they have rate limits instead.

## Results

Each file holds the model, provider, prompt version, git commit (and whether the tree was
dirty), the cap and spend, a "k of n" summary per strategy and check, and one row per run.
Failed runs carry a trace (mock data only). Results are published including failures
(requirements E1), and small samples support no calibration claims (E3).

## Strategies

| Strategy | What it is |
|---|---|
| `one_shot` | The model answers from the prompt with no tools. Every id it names is a guess, so this measures hallucinated entities. |
| `fixed_workflow` | Code, no model: always order in, best open restaurant, one in-budget item. Shows what path choice adds. |
| `agent` | The full loop: tools, questions, the plan validator, replanning. |
| `agent_no_validator` | The same loop with the plan validator off: whatever the model proposes reaches the approval screen. The scorer still validates the final plan, so `plan_valid`, `hard_constraints` and `no_hallucinated_entities` show what the validator would have caught. Run it next to `agent` on the same model: `--strategies agent,agent_no_validator`. |

