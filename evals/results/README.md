# Eval results

All runs are on the synthetic mock world. Counts are "k of n"; failures are published (requirement E1).

## Top level (the files to read)

- `2026-10-07-gpt-5-mini-full.json`: **the full run**: 24 scenarios x (3 `agent`, 3 `agent_no_validator`,
  1 `fixed_workflow`), model gpt-5-mini, prompt agent_v5. Built from three parts by
  `scripts/merge_results.py` (see `merged_from` inside). Two runs whose model call stalled while
  the laptop slept are excluded (`excluded_runs`), and three `fallback_plan` failures were
  re-scored from their traces after the scorer's word list was corrected (`rescored_fallback_plan`).
  One S-23 `plan_size` failure was re-scored after the owner raised that scenario's limit from 2 to 6
  items (`rescored_plan_size`).
  Total spend about Rs 135 at Rs 97 per dollar.
- `2026-10-07-qwen-qwen3.8-27b.json`: the first real smoke run (6 scenarios, prompt agent_v4, Groq free tier).
  Kept because its failures led to code changes (see `docs/case-study-notes.md`) and to a replay.

## `parts/`

The three raw parts of the full run, as the runner wrote them (one was interrupted; one ended
on a dropped connection). Kept for provenance; read the merged file instead.

## `archive/`

Early or aborted attempts: a 6-run gpt-5-mini smoke check and six Groq attempts that ended in rate limits
or an oversized request (HTTP 413). Mostly invalid runs; kept for the record, not for analysis.

Progress files (`*.partial.json`) are git-ignored.
