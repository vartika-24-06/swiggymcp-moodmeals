# Demo video script (T8.3)

A 3 to 4 minute screen recording. Draft: edit freely. The goal is the story the project tells:
**one vague "kya khana hai" becomes one grounded, approvable plan, and the agent knows its limits.**

## Before you record (privacy, requirement R12.4)

- Use a **test Swiggy account or full redaction**. Never show an address, phone number, OTP,
  name of another person or order history.
- **Sign in to Swiggy before you start recording.** Do not record the sign-in pages: they show a phone number.
- In dry-run, keep **"Hide address text" ticked**. The picker then shows only labels ("Home", "Work").
- Close other browser tabs and hide bookmarks (they show on screen). Use a clean browser window.
- Put the API key in the terminal (`$env:GROQ_API_KEY = "..."`) or use the scripted demo. Never type a key on screen.
- Do not show the terminal while it holds paths or tokens. Crop to the browser.
- Do a dry run of the whole script once without recording.

## Setup (not recorded)

```powershell
git pull origin main
pip install -e ".[dev]"
streamlit run app/Home.py                                   # mock mode: made-up restaurants
# For the real-data part (dry-run, read-only, nothing is ever ordered):
$env:MOODMEALS_MODE = "dry_run"; streamlit run app/Home.py
```

## Scenes

**1. The problem (15 s, voice only or a title card).**
"Too many options and I end up ordering nothing. MoodMeals decides: cook (Swiggy Instamart) or order in (Swiggy Food). At most three questions, then one plan, and nothing happens without my approval."

**2. A happy path in mock mode (60 s).** Plan page, mock mode, scripted demo model.
- Click "Thaka hua hoon, kuch halka". Point at the single question (diet), then **Plan my meal**.
- Show the plan card: one dish, the price, "Item total", the assumptions and the disclaimer.
- Open **Trace: what the agent did**: tool calls, questions, tokens, time. "Every restaurant and price here came from a tool result in this run."
- Click the approve button: **step 1 of 2, cart**, then **step 2, order**. "Two separate approvals. This is the simulated mock world; nothing real happens."

**3. It knows when it can't (45 s).** Replay page, "Every restaurant is closed".
- "When ordering in is impossible it says so and offers a ready-to-eat item from Instamart, instead of forcing a plan from a closed restaurant."
- Then replay "Failure (real model): gave up without trying Instamart" (marked ⚠️). "This is a real failure from a real model. It led to a code rule. Failures are published."

**4. Real data, read-only (60 s, optional).** Dry-run mode.
- "Connect to Swiggy" is already done. Pick an address from the labels only.
- Plan a meal; show real restaurant names and prices on the plan card (check there is nothing personal).
- Approve: show **"Dry-run: approving only shows what would happen. Nothing is sent to Swiggy."** and the "What would have happened" panel. "No cart or order is touched."
- Optional close: show your Swiggy app cart is empty.

**5. What I'd show an interviewer (20 s).** Evals page or `evals/results`: "24 scenarios, three strategies, k-of-n results including failures" (only once T7.5 and T7.6 are done; otherwise skip).

**6. Close (10 s).** "Planning aid, not advice. Not affiliated with Swiggy. Code and write-up are on GitHub."

## Checklist before you publish

- [ ] No address, phone, OTP, name or order history anywhere in the video.
- [ ] No API key visible. No browser bookmarks or personal tabs.
- [ ] The About page wording is approved (T5.5) and the "not affiliated with Swiggy" line is visible.
- [ ] You say that dry-run places no cart or order, and that mock mode is made up.
