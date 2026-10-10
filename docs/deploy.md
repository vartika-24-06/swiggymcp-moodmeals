# Public deployment (T8.6): mock mode only, your steps

The public site runs **mock mode only**. It has no Swiggy sign-in, makes no Swiggy call, and
refuses dry-run and live at startup. Visitors can use the scripted demo (no key), replay, the
results page, or their own model key.

## Steps (Streamlit Community Cloud)

1. Push `main` to GitHub (already done by the session). Make sure the repository is public or
   that your account can deploy private repositories.
2. At share.streamlit.io choose **Create app** and pick this repository, branch `main`, main
   file `app/Home.py`.
3. Under **Advanced settings**, choose Python 3.11 or newer and add these secrets/env values:

   ```
   MOODMEALS_DEPLOY = "public"
   ```

   Do **not** set `MOODMEALS_MODE` or `MOODMEALS_ALLOW_LIVE`. If someone sets either to a
   non-mock value on a public deployment, the app refuses to start.
4. Deploy. Dependencies come from `requirements.txt` (`-e .`, which reads `pyproject.toml`).
5. Do not add any model key as a secret: visitors bring their own, held in memory per session.

## Check it after deploying (five minutes)

- Open the link in a private window. Pick **Scripted demo (no key)** and plan a meal: it must
  reach the approval screen with a "mock" label and no Swiggy connect button.
- Open **Demo video**, **Replay** and **Results**: all three load. The ℹ️ next to the title opens a short note on why the site is mock only.
- Optional: paste a low-limit key for one run, then confirm nothing in the app shows the key
  afterwards (not in the trace, not in an exported run).

## What the app guarantees on the public site

| Rule | Where it is enforced |
|---|---|
| Mock mode only | `app/config.py` `resolve_mode`, tested in `tests/test_app.py` |
| No Swiggy connect control | `app/Home.py` shows it only in dry-run or live, which the public site refuses |
| Key kept in memory, never saved or logged; the pass-through notice is shown | session state only; trace redaction tested |
| No order, checkout or payment tool exists | no such tool in any allowlist |

## If something fails

- *App will not start, "The public site runs mock mode only"*: remove `MOODMEALS_MODE` from the
  deployment's environment.
- *Import error for `moodmeals`*: check `requirements.txt` still contains `-e .`.
- *Results page empty*: `evals/results/` must be in the deployed branch.
