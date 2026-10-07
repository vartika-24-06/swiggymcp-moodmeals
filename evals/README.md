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

Options: `--set smoke|full`, `--strategies` (comma list of `one_shot`, `fixed_workflow`,
`agent`), `--runs N` (model strategies only; the fixed workflow is deterministic and runs
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
