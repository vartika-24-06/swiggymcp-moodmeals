# Spikes

One-off experiments that answer open questions before the real build. Spike code
is not part of the product and is not imported by `moodmeals/`.

## Spike A: Swiggy sign-in from Python (tasks.md T0.2, design DQ1, requirements Q2)

**Question:** can the official `mcp` Python SDK complete Swiggy's OAuth sign-in
(phone and OTP) on localhost, and then make a read-only call?

**What the script does** (`swiggy_signin.py`):

1. Connects to `https://mcp.swiggy.com/food` over streamable HTTP.
2. Runs OAuth 2.1 with PKCE. It opens the Swiggy sign-in page in your browser;
   the redirect comes back to `http://localhost:8765/callback` on your machine.
3. Makes **one** read-only call, `get_addresses`.
4. Prints **only the number** of saved addresses.

**What it does not do:** it never prints, logs or saves address text, phone
numbers or names. Tokens are kept in memory and are gone when the script exits.
It calls no cart, order, checkout, address-changing or payment tool.

**Run it on your own Windows laptop, not in the Codespace.** The redirect goes to
`localhost` on the machine where the browser runs.

### Windows steps

You need Python 3.11 or newer. Check in PowerShell:

```powershell
py --version
```

If that fails or shows an older version, install Python from python.org (tick
"Add python.exe to PATH") and reopen PowerShell.

1. Get the code and go to the repo folder:

   ```powershell
   git clone https://github.com/vartika-24-06/swiggymcp-moodmeals.git
   cd swiggymcp-moodmeals
   ```

   (If you already cloned it: `cd` into it and run `git pull`.)

2. Create and activate a virtual environment:

   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   If PowerShell says running scripts is disabled, run this once and then
   activate again:

   ```powershell
   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
   ```

   (In the old Command Prompt, activate with `.venv\Scripts\activate.bat` instead.)

3. Install the one dependency:

   ```powershell
   python -m pip install -r spikes\requirements.txt
   ```

4. Run the spike:

   ```powershell
   python spikes\swiggy_signin.py
   ```

5. Your browser opens the Swiggy sign-in page. Enter your phone number and OTP
   there (the script never sees them). When the browser tab says the sign-in
   step finished, go back to PowerShell.

Options: `--port 9000` uses a different redirect port (if 8765 is taken);
`--no-browser` prints the sign-in URL instead of opening it.

### What you should see

```
Opening the Swiggy sign-in page in your browser...
Signed in. MCP session is open.
SUCCESS: saved addresses = <a number> (from pagination.total)
```

### What to send back

Copy the terminal output from the first line the script printed. It is one of:

- **`SUCCESS: saved addresses = N ...`**: send that line. (The number alone is
  not personal; leave it out if you prefer and just say "success".)
- **`PARTIAL: ...`**: sign-in worked but the response shape was not what the
  script expected. Send all the lines. They hold only key names and value types
  (for example `"addressLine": "str"`), never values.
- **`FAILED: <ErrorType>: <message>`**: send the exact line(s), and say at which
  point it failed: before the browser opened, on the Swiggy page (what the page
  said, in your words), or after you were sent back to localhost.

Before sending, read it once. It should contain no address, phone number or
name; the script masks any run of 6 or more digits as `<digits>`. If a
`Could not open a browser` line appears, do not send the long URL under it
(it is not personal, just not needed).

Also useful: the output of `python --version` and
`python -m pip show mcp` (the `Version:` line only).

### Things I am unsure about (not guessed, to be settled by this run)

- **Client registration.** The script gives the SDK no pre-registered client
  id. The SDK then does OAuth *dynamic client registration* by itself. What I
  could check without signing in: Swiggy's public OAuth metadata
  (`https://mcp.swiggy.com/.well-known/oauth-authorization-server`, read on
  2026-10-04) does advertise a `registration_endpoint`, PKCE `S256`, and the
  `none` token-endpoint auth method the script asks for. What I could **not**
  check: whether that endpoint accepts a registration from an unknown client,
  or only from clients Swiggy has allow-listed. If it refuses, expect a
  `FAILED: OAuthRegistrationError` line.
- **Redirect address.** Requirements A1 says localhost redirects are allowed.
  I do not know if Swiggy restricts the port, the path (`/callback`) or
  `localhost` versus `127.0.0.1`. If the Swiggy page shows a redirect-URI error,
  tell me its wording.
- **Resource check.** The server's metadata names its resource as
  `https://mcp.swiggy.com` while we connect to `/food`. Reading the SDK source,
  it accepts a parent resource, so this should pass; it is untested.
- **Response shape.** The count is read from `pagination.total`, which the
  tool's description documents. I have not seen a real response from this
  client, hence the `PARTIAL` branch.
- **SDK version.** The script was written against `mcp` 2.3.0 by reading its
  source (`Client`, `OAuthClientProvider`, `streamable_http_client`). It was
  linted and its local callback server was tested offline, but the script was
  **never run against Swiggy**. `mcp` 2.x uses `httpx2` (installed with it), not
  `httpx`.

### After the run

The result and any fallback go into `spikes/notes.md` (T0.2 "done when"). If
sign-in fails, the fallback in tasks.md applies: the live demo signs in through
another MCP client and nothing else changes.

## Spike B: tool-calling reliability (tasks.md T0.3, design DQ3)

**Question:** which models can drive our loop, where every turn must be exactly one
action (a tool call, `ask_user` or `propose_plan`) with arguments that match the schema?

**What it does** (`tool_calling.py`): sends 5 synthetic prompts (English, Hinglish, a
vague one, and one that must build a plan from given results), 5 times each, to each
model you name. It runs two modes: `native` (the provider's function calling) and
`json` (plain JSON reply, our possible fallback). It scores:

- **valid**: exactly one action, a known name, arguments matching the schema
- **right**: valid and also the sensible action for that prompt

It uses only Python's standard library, so there is nothing to install. It sends no
Swiggy or personal data, and it saves only counts, never prompts, replies or keys.

### Steps (PowerShell, from the repo folder, `.venv` active)

1. Check the scoring code works offline: `python spikes\tool_calling.py --selftest`
   (should print `selftest ok`).
2. Set the key for each provider you want to test, in this terminal only:

   ```powershell
   $env:GEMINI_API_KEY = "paste-key-here"
   $env:GROQ_API_KEY = "paste-key-here"
   $env:OPENROUTER_API_KEY = "paste-key-here"
   $env:OPENAI_API_KEY = "paste-key-here"
   $env:ANTHROPIC_API_KEY = "paste-key-here"
   ```

   Closing the terminal forgets them. Never paste a key into a file or into chat.
3. Find exact model IDs by asking the provider, for example
   `python spikes\tool_calling.py --list-models gemini` (also `openai`, `groq`). It
   prints IDs only. Names change, so copy them from this list, not from memory. Prefer free-tier or small models. Then run, one
   `--run provider:model` per model, for example:

   ```powershell
   python spikes\tool_calling.py --run gemini:<model-name> --run groq:<model-name>
   ```

   Providers: `gemini`, `groq`, `openrouter`, `openai`, `anthropic`.
4. Free tiers rate-limit. The script waits 2s between calls and retries a 429 twice.
   If you still see many errors, raise `--delay 6` or run one model at a time.
   Each model makes 50 calls (5 prompts x 5 repeats x 2 modes). Use `--repeats 3` for
   a cheaper first pass.

### What to send back

Paste the table and per-prompt lines the script prints at the end. They contain no
keys and no prompt text. Also say which exact model names you used.

## Spike C: shape of real responses (tasks.md T0.4, design DQ2)

**Question:** what do Swiggy's real responses look like, so the mock world matches
them and we can choose which fields the model sees (compaction)?

**What it does** (`capture_shapes.py`): signs in like Spike A, then makes a few
read-only calls (addresses, a restaurant search, one menu, a dish search on Food; a
product search on Instamart). It writes two things:

- `spikes/captures/<server>/<tool>.json`: the raw responses. **Local only and
  git-ignored.** Nothing reads them out loud; they are for the offline token check.
- `spikes/results/shapes_<server>.json`: a **shape report** that is safe to commit.
  It has field names, types, list lengths, number ranges, text formats with every
  letter and digit masked, and the values of a few enum-like fields (VEG, OPEN...).
  Any field whose name contains address, phone, email, area, location, user, token
  and similar words is never read at all.

It calls no cart, order, checkout, address-changing, order-history or payment tool.
`get_addresses` is called only to get an address id, which stays in memory.

### Steps (PowerShell, repo folder, `.venv` active)

1. `git pull`, then `python spikes\capture_shapes.py --selftest` (prints `selftest ok`).
2. Food: `python spikes\capture_shapes.py --server food`
   Sign in in the browser as before. It takes about a minute (each call can take
   ~15 s). It prints one line per tool at the end.
3. Instamart (a second sign-in, since it is a separate server):
   `python spikes\capture_shapes.py --server im`
4. **Read the shape reports once** (`spikes\results\shapes_food.json`, `shapes_im.json`).
   They should contain no address, phone number or person's name. Restaurant and
   product names are masked to `Aaaa Aaaa` form. If anything looks personal, do not
   share it; tell me which line.
5. Commit and push the two shape reports (not `spikes\captures\`, which is ignored),
   or paste the terminal summary and I will ask for the files if needed.

### What to send back

The summary the script prints (tool names, status, rough token counts), and whether
any tool says `skipped` or `tool error`. Do not paste anything from `spikes\captures\`.

### Spike C, part 2: compaction numbers (offline, no sign-in)

After `capture_shapes.py` has run for `food` and `im`, run:

```powershell
python spikes\compact_check.py
```

It reads your local raw captures, applies the real parsers and the model view, and
prints only numbers: size before and after, items parsed versus items in the raw
response, and counts of veg and sponsored values. It prints no names, ids or text and
writes nothing. Send me the output.
