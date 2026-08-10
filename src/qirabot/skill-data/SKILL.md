---
name: qirabot
description: "Drive any GUI with AI vision on raw screenshots — no DOM, no CSS/XPath selectors — via the Qirabot Python SDK or the qirabot CLI. Hand it a whole goal to complete autonomously, or make single natural-language actions: click, type, extract, and verify on web browsers, Android, iOS, desktop apps, and games. Use this when the user wants to automate, test, or scrape a UI by describing elements in plain language; when driving a mobile app, native desktop app, canvas UI, or game that DOM-based tools like Playwright/Selenium alone can't reach; when adding AI steps to an existing Playwright/Selenium/Appium/pyautogui or pytest setup; or for visual UI verification, screenshots, and RPA. Triggers include: automate a website or app, UI/end-to-end test, fill a form, scrape a page, tap or click a button, verify what's on screen, drive an Android/iOS app or desktop application, automate a game, add AI element location to existing tests, run a one-shot automation task from the terminal."
license: MIT
metadata:
  author: qirabot
---

## Step 0 — Preflight (always run first)

Do NOT write an automation script before the environment checks out:

```bash
python scripts/preflight.py browser     # or: android | ios | desktop
```

**If it fails, stop and fix what it prints** — every failing check comes with
the exact fix command. (After qirabot is installed, `qirabot doctor` is the
packaged complement: it also probes that the Google Cloud credentials (ADC)
work, and exits 0/1 so CI can gate on it.)

**One interpreter, one source of truth.** Preflight validates *one* Python and
prints its absolute path. Run your script with that exact path — and the CLI
via the binary next to it (`<venv>/bin/qirabot`) — never a bare
`python`/`qirabot` from PATH, or the run drifts to an env you didn't check
(the #1 false-"Ready" trap). Reuse whatever validated env exists; don't force
a new venv.

Bootstrap only when preflight reports something missing — prefer a venv:

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install "qirabot[browser]"    # then: playwright install chromium
#  or qirabot[appium] / qirabot[desktop]; Android/iOS/Windows-window are built in
gcloud auth application-default login   # one-time Google Cloud auth (ADC) — or set
#   GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
#   or, gemini-vertex models only: QIRA_VERTEX_API_KEY=<Vertex AI API key> (no gcloud needed)
#   or, no Google Cloud at all: QIRA_GEMINI_API_KEY=<AI Studio key> + QIRA_MODEL=gemini/gemini-3.6-flash
echo 'QIRA_MODEL=gemini-vertex/gemini-3.6-flash' > .env   # optional: pin the model
```

All extras install cleanly together (no numpy/opencv pins anywhere): one
`.venv` with `qirabot[all]` covers every backend.

## Step 1 — CLI or SDK? Pick the invocation path

Two front doors. Rule of thumb: *one instruction, a human reads the result →
CLI; the script must branch or read values → SDK.* Judge by who consumes the
result, not by what the task does: scraping or reporting info *for the user
to read* is still CLI — put the ask in the goal string; `extract()` only
earns SDK when **your code** uses the returned value.

**CLI — the default for one-shot goals.** No script file to write; a live
per-step trace, Ctrl+C → *cancelled* (exit 130), and non-zero exit on failure
are built in, so it's a direct CI gate. Full flag matrix, engine rules
(direct vs appium), and recording setup: `references/CLI.md`. On this path,
skip straight to Step 4's result-checking guidance.

```bash
qirabot browser "Find the cheapest laptop and report its price" -u example.com
qirabot android "Turn on airplane mode" -d emulator-5554
qirabot ios "Enable Bluetooth in Settings" --bundle-id com.apple.Preferences
qirabot desktop "Compute 12*34 in the calculator" --app Calculator
```

**SDK — when code must consume results or the flow has logic:**
`extract()`/`verify()` branching, deterministic primitives mixed with `ai()`,
several `ai()` calls in one session, `wait_for` gates, or a `bind()` of your
own Selenium/Appium driver. Continue with Step 2.

Utility commands help on both paths: `qirabot doctor` (env check) and
`qirabot models` (the built-in Vertex providers and their default models —
the valid `-m`/`QIRA_MODEL` values).

## Step 2 — SDK path: pick a target and start from a template

| Target | Template | Extra |
|---|---|---|
| Web browser (launches Chromium; or attach to running Chrome via `cdp_url`) | `templates/browser.py` | `qirabot[browser]` + `playwright install chromium` |
| Android — direct adb (built in, no server, fastest start) | `templates/android.py` | core `qirabot` + the adb binary |
| iOS — direct WDA (built in; real device) | `templates/ios_wda.py` | core `qirabot`; WDA running on :8100 (`iproxy 8100 8100`) |
| iOS — Appium XCUITest (simulators, auto WDA build/sign) | `templates/ios_appium.py` | `qirabot[appium]` + running Appium server |
| Any Selenium driver / other Appium targets (`bind()` your driver) | `templates/bolt_on.py` | `qirabot` + your framework |
| Desktop — Windows & macOS | `templates/bolt_on.py` | `qirabot[desktop]` (whole screen, any OS) · core `qirabot.Window` (Windows, one window) |
| Anything else — airtest, cloud-device SDKs, your own backend | custom `DeviceAdapter` — see "Custom adapters" in `references/REFERENCE.md` | core `qirabot` + your framework |

Copy the template, fill in the `TODO`s, run it with the preflight-echoed
interpreter. iOS has two starters: default to `ios_wda.py` when the user
wants the minimal path or WDA already answers (`curl http://127.0.0.1:8100/status`);
pick `ios_appium.py` for "appium", simulators, or when the script needs
Appium's device APIs. The platform gotchas (iOS 17 app launch, WDA reuse) are
already handled inside the templates; the per-platform action matrix and
`bind()` details are in `references/REFERENCE.md`.

## Step 3 — Hand the whole task to `bot.ai` (default)

```python
from qirabot import StepResult

def on_step(step: StepResult) -> None:   # ALWAYS pass this — the only live view
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")

result = bot.ai(target, "Add the cheapest item to the cart and check out",
                max_steps=15, on_step=on_step)
print(result.success, result.output)
```

`bot.ai` runs the perceive → decide → act loop locally in the SDK —
screenshots go straight to your own Vertex AI model — and self-heals when
a step misfires. **Keep the task string a concise goal, not a step-by-step
script** — over-specifying ("click Search, then type X, then…") fights the
model, locks in a brittle path, and burns steps; write what success looks
like. **Always pass `on_step`** — without it the run is a black box until it
returns; the printed `decision` trace is how a stuck or looping run gets
debugged from stdout.

If you `bind()` a stable target first (as the android/ios/bolt_on templates
do), drop the leading arg: `bot.ai("...")`, `bot.click("...")`. Keep the
explicit `page = bot.click(page, ...)` form for Playwright so new-tab switches
stay visible.

When the task needs something the UI can't do, pass
`custom_tools=[your_function]` — any Python function: call a backend endpoint,
query a database, fetch an OTP, pause for a human to pass a captcha. The model
calls it mid-task, it runs locally, and the return value feeds back as the
observation. `exclude_tools=["..."]`
prunes built-in actions the task never needs.

When the model would get stuck for lack of domain context (game rules,
business terms), pass `knowledge=` — the text, a `Path` to a local file, or a
list of both. It rides in the system prompt as reference material, per call,
so each stage loads only its own knowledge. Rules and details:
`references/REFERENCE.md`.

**Drop to per-step primitives only as a deliberate optimization** — strict
determinism or a stable flow run repeatedly (e.g. CI): cheaper per action,
reproducible, but brittle to UI changes:

```python
bot.click(target, "Login button")
bot.type_text(target, "Email field", "a@b.com", press_enter=True)
bot.long_press(target, "the message bubble", duration=2.5)   # mobile context menu
bot.key_down(target, "w"); bot.key_up(target, "w")           # desktop hold (pair them)
text = bot.extract(target, "the displayed account balance")
bot.wait_for(target, "the dashboard finished loading")       # gate, raises on timeout
```

Full API — constructor options, `bind()`, navigation/scroll/keys, per-platform
action matrix, errors: `references/REFERENCE.md`.

## Step 4 — Check the result by who consumes it

Every run writes `./qira_runs/<date>/<run>/report.html` with per-step
screenshots (unless `report=False`). Pick the signal by who acts on it:

- **A human/agent reads the result** → open the report and look at the step
  screenshots. Do NOT trust `result.success`: it's the acting model grading
  itself, and can claim victory after clicking the wrong button.
- **The script must branch** (CI gate, skip-if-logged-in) →
  `bot.verify(target, "...")` — an independent vision call returning `bool`.
  The bool must drive something; otherwise it's an extra model call (token
  cost) whose answer the screenshot already gives for free.
- **The script needs a value** (price, username, status) →
  `bot.extract(target, "...")`. Scope the phrase — an ambiguous locate can
  grab a look-alike element — and cross-check against the screenshot.

When a run fails, the **stdout step trace is the fastest entry point**: find
where the model started looping or chose wrong, then jump to that step's
screenshot in the report. (CLI runs print the same trace automatically; the
exit code stands in for `result.success`.)

To embed a screen video in the report, have `recording.mp4` in
`bot.report_dir` before `close()`: `Qirabot(record=True)` records the host
screen (desktop/browser); for Android/iOS record the **device** screen via
`record_device=True` / `record_mjpeg_url` — see the `record*` rows in
`references/REFERENCE.md`.

## Notes

- One script run = one session = one task; the live page/driver does not
  survive across `python` invocations, so put a whole task in one script. To
  reuse a login across runs, open with a persistent profile
  (`user_data_dir=...`, ABSOLUTE path — `~` is not expanded; see
  REFERENCE.md "Persistent login").
- **Windows desktop/games:** if the target app runs as Administrator, run the
  script elevated too — else Windows UIPI silently drops clicks/keystrokes
  (cursor moves, nothing happens, no error).
- **Confirm before irreversible or outward-facing actions** done under the
  user's identity (posting, purchasing, deleting): read first, report exactly
  what you're about to do, get the user's go-ahead, then act — as separate
  steps.
- Costs real tokens per AI call — on your own Vertex AI project (there is no
  Qirabot account or per-step fee). Pick the cheapest `model=` that fits (e.g.
  a flash-class model, stepping up to a pro-class one only when needed). Long
  human-in-the-loop waits (QR/OTP) poll with model calls — raise the
  `wait_for` interval or poll the live driver free (see REFERENCE).
- Always `bot.close()` (or the `with` form) — it finalizes the task and writes
  the report, even on error.
