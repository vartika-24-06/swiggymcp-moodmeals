# Final pass against the definition of done (T8.7, 2026-10-08)

Checked against requirements section 11. Every box is ticked or carries an honest note.

| Item | Status | Evidence |
|---|---|---|
| R1–R5 and R7–R15 implemented and traceable to tests or eval scenarios | Done, with notes | 24 scenarios in `evals/scenarios/`, about 400 tests. Notes: R15.2 (video) is the owner's, T8.3. |
| All writes proven gated by tests | Done | `tests/test_loop.py`, `tests/test_live_guards.py`, `tests/test_app.py`; the eval check `no_writes_without_approval` is 71 of 71, 71 of 71 and 24 of 24 across strategies. Only cart updates exist, live mode only. No order, checkout or payment tool is in any allowlist. |
| Evals for strategies 1–3 with published results, including failures | Done | `evals/results/2026-10-07-gpt-5-mini-full.json`, shown on the Results page with every failed run. Honest note: the agent's 71 of 71 follows fixes made after the first run (65 of 71); both are published, with the prompt version per scenario. One model only. |
| Replay gallery with at least 6 runs (including 2 failures) | Done | `data/replays/`: 4 successes, 2 failures. |
| Demo video recorded with personal data hidden | **Open, owner** | Script ready in `docs/demo_script.md` (T8.3). |
| README and case study state the limits, A3 and the no-endorsement rule | Done | `README.md` (limits section, first paragraph), `docs/case-study.md`, About page. A3 is stated in README limits and case study. |
| Eval spend within the cap | Done | About ₹161 of the ₹1,500 cap, including reruns. |

## Other checks run in this pass

- `ruff check .` and `pytest` pass.
- Repository scan for keys, tokens and phone-number patterns: only obviously fake test values
  (such as `9876543210`) in tests and spikes. No credentials or token files are tracked.
- No Swiggy logo or wording implying endorsement in `app/`; About says "not approved, sponsored
  or endorsed".
- Every page renders in public mode, and dry-run or live is refused there (`tests/test_app.py`).

## Still open (not blockers for the code)

- T8.2 name and branding check (owner).
- T8.3 recording the demo (owner).
- T8.6 the actual deployment, using `docs/deploy.md` (owner).
- T6.4 one optional live cart update; skipped.
- The real Food cart reply with items is unconfirmed (stated in README).
- Optional: re-run the two stalled runs (S-10, S-14) for about ₹2 to make the run counts complete.
