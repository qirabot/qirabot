# Qirabot API reference (condensed)

Quick reference for the skill. The SDK README is the full source of truth; this
mirrors the parts an agent needs to write a script. Keep in sync with the SDK.

## Construct

```python
from qirabot import Qirabot
bot = Qirabot()                       # model from QIRA_MODEL env, else the built-in default
```

Auth is Google Cloud ADC: set `GOOGLE_APPLICATION_CREDENTIALS` to a
service-account JSON, or run `gcloud auth application-default login` once.

Common constructor options (all keyword):

| Option | Default | Notes |
|---|---|---|
| `model` / env `QIRA_MODEL` | built-in default | `"{provider}/{model}"`, e.g. `"gemini-vertex/gemini-3.6-flash"`; providers: `claude-vertex` \| `gemini-vertex` (list with `qirabot models`) |
| `vertex_project` / `vertex_location` | ADC project / `global` | Google Cloud project/location for Vertex AI (env `QIRA_VERTEX_PROJECT` / `QIRA_VERTEX_LOCATION`, fallback `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`) |
| `thinking_level` | model's setting | `minimal` \| `low` \| `medium` \| `high` — per-task thinking override; every action method also takes a per-call `thinking_level=`. Effective granularity depends on the underlying model (some levels may be merged or clamped). |
| `language` | `"en"` | response language tag, e.g. `"zh"`, `"en"` |
| `task_name` | `""` | shown in the run report header |
| `report` | `True` | write HTML report on close |
| `report_dir` / env `QIRA_REPORT_DIR` | `./qira_runs/<date>/<run>/` | output root |
| `record` / env `QIRA_RECORD` | `False` | ffmpeg recording of the **host machine's screen** into `report_dir/recording.mp4`, auto-embedded in the report — NOT the device screen. For mobile use `record_device`/`record_mjpeg_url` below, or drop your own `recording.mp4` into `report_dir` before `close()` (Appium: `driver.start_recording_screen()` then b64-decode `driver.stop_recording_screen()` to that path; stop before `close()` — it scans for the file). |
| `record_device` / env `QIRA_RECORD_DEVICE` | `False` | Implies `record`: record the automated **device's** screen instead of the host's, resolved from the first action's target — Appium driver → session recording API (auto-stopped before the report; if you `driver.quit()` yourself, call `bot.stop_recording()` first), `qirabot.AdbDevice` → `adb screenrecord`. Unsupported targets skip recording rather than capture the wrong (host) screen. |
| `record_mjpeg_url` / env `QIRA_RECORD_MJPEG_URL` | `None` | Implies `record`: record this MJPEG-over-HTTP stream instead of the host screen — in practice WDA's iOS device stream on port 9100 (USB real device: `iproxy 9100 9100`). Needs ffmpeg. |
| `record_fps` | `12` | recording frame rate (host-screen recorder) |
| `record_window` / env `QIRA_RECORD_WINDOW` | `False` | **Windows window backend only.** Record just the window under test (auto-resolved from the first action's target) instead of the full desktop; any other backend or resolution failure falls back to full screen. By default it crops a desktop grab to the window's visible rect, so **GPU/game windows record correctly** (keep the window visible/foreground — whatever overlaps the rect is captured). Set `QIRA_RECORD_WINDOW_NATIVE=1` to force legacy `gdigrab` per-window capture (can follow a background/occluded *non-GPU* window, but is black/frozen for minimized/GPU game windows). Manual override: `start_recording(window="Title")`. |
| `record_audio` / env `QIRA_RECORD_AUDIO` | `False` | **Windows only**, any backend. Capture **system audio** into the recording. ffmpeg has no native loopback, so needs a DirectShow source: install screen-capture-recorder (`virtual-audio-capturer`) or enable "Stereo Mix". Auto-detected; override with `record_audio="Device"` or `QIRA_AUDIO_DEVICE`. Missing device → silent + warning. |
| `record_audio_offset` / env `QIRA_AUDIO_OFFSET` | `None` | A/V sync offset in seconds (usually negative, e.g. `-0.4`) applied to the audio input. |
| `screenshot_annotate` | `True` | red crosshair at click/type point |
| `retry` / `retry_delay` | `1` / `1.0` | per-action retry on transient failure |
| `settle_seconds` / env `QIRA_SETTLE_SECONDS` | per-platform | fixed pause after each action |

## Default: hand the task to `bot.ai`

```python
result = bot.ai(target, instruction, max_steps=20, *, on_step=None,
                thinking_level="", language="", custom_tools=None, exclude_tools=None,
                knowledge=None)
# result.success -> bool, result.output -> str (final answer)
```

Runs the full perceive → decide → act loop locally in the SDK — each step
sends a screenshot to your configured Vertex AI model, with step history
managed in-process (self-heals on a misfire). Prefer this over hand-sequencing
primitives. Drop to the primitives below only for strict determinism or a stable
flow you'll run repeatedly (e.g. CI).

**`on_step(step)` — your live window into the run.** It fires after every step;
`bot.ai` is otherwise a black box until `result` returns. Print it so a stuck,
looping, or failed run is debuggable from stdout without opening the report:

```python
def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")
```

`StepResult` fields: `.step`, `.action_type`, `.params`, `.finished`,
`.decision` (the model's reasoning for this step), `.output` (text on the final
step), and `.input_tokens` / `.output_tokens` / `.thinking_tokens` /
`.step_duration_ms` / `.llm_decision_duration_ms`. The full list is also on
`result.steps` after the run; `on_step` is the same data, live. (The callback is
read-only — its return value is ignored and it can't steer the loop. It runs on
the hot path, so keep it light and wrap any IO in `try`; an exception thrown here
aborts the run.) A lighter alternative if you only want a trace, not structured
data: `logging.basicConfig(level=logging.INFO)` — `bot.ai` already logs each step
at INFO.

**`bot.usage` — session-wide token/step totals.** A frozen snapshot covering
every AI call on the client so far — `ai()` steps, AI-located actions
(`click()`/`type_text()`/…), and standalone `verify()`/`extract()`/`locate()`;
failed calls' spend is included, `.ai_steps` counts successful calls only.
Fields: `.ai_steps`, `.input_tokens` (non-cached prompt portion),
`.cache_read_tokens` / `.cache_write_tokens` (cached prompt portion),
`.output_tokens` (already includes thinking), `.thinking_tokens`,
`.total_tokens` (input + cache read/write + output), `.step_duration_ms`,
`.llm_decision_duration_ms`. Read it after a run for cost accounting; the
CLI prints the same summary after each task, and the HTML report header
shows it too.

**`custom_tools` — register your own functions as tools the model can call
mid-task.** Any Python function works — anything code can do (internal API
call, database query, OTP fetch, test-data seeding, human-help pause; a GM
command in game testing is just one example). Pass named functions: name/description/parameter schema are introspected from
the function name, **docstring (required)**, and signature (annotations
`str`/`int`/`float`/`bool`; params without defaults = required; lambdas and
`*args`/`**kwargs` rejected; max 16 tools). The handler runs **locally** — the
model only sees the tool's name/schema and return value, never your
endpoint/credentials — and its return value is fed back
to the model as the observation (`None` → `"ok"`; a raised exception → `ERROR:
...` so the model can react). Dict escape hatch for richer schemas:
`{"name", "description", "parameters", "handler": fn}`.

**`exclude_tools`** removes built-in tools by name (e.g. `"long_press"`) from
the model's tool list for this call — prune actions the task never needs so the
model can't wander into them. `done` cannot be excluded. Excluding an action the
task actually requires (e.g. `"click"` on a browsing task) strands the run.
Both params work on bound calls too.

**`knowledge` — domain background the model consults while deciding** (game
rules, business flows, terminology), kept separate from `instruction` so
reference material is never mistaken for the goal. Accepts the text itself
(`str`), a local file (`pathlib.Path`, UTF-8), or a list mixing both; combined
limit 32 KB. A plain `str` is always literal text — never a filename. For
remote sources fetch the text yourself (`requests.get(url).text`) and pass it.
Like the tool params, it works on bound calls.
Per-call scope makes staged loading natural: each `bot.ai` stage passes only
its own knowledge, and dropping a stage's knowledge (or a tool) is just not
passing it to the next call. Hard limits ("GM may be used once") belong in the
custom tool's handler code — prompts persuade, code enforces:

```python
gm_used = False
def gm_command(cmd: str) -> str:
    """发送 GM 命令。整个任务只允许使用一次。"""
    global gm_used
    if gm_used:
        return "GM 命令已使用过，请用常规操作完成目标。"
    gm_used = True
    return send_gm(cmd)

bot.ai(win, "完成新手引导", custom_tools=[gm_command],
       knowledge=Path("game_rules.md"))       # 阶段知识随调用挂载
bot.ai(win, "完成每日副本", knowledge="副本每日上限 3 次")  # 上一阶段的 GM 工具与知识自然卸载
```

## Bind (drop the repeated first arg)

Every action's first argument is the framework object (`page`/`driver`/device).
For a single stable target, `bind()` once:

```python
bot = Qirabot().bind(driver)   # Selenium/Appium driver, pyautogui, AdbDevice/WdaClient/Window,
                               # or a custom DeviceAdapter instance (see "Custom adapters")
bot.ai("complete the task")    # no target arg
bot.click("Login")
bot.current_page()             # reach the live page from a bound proxy
```

Recommended for adb/WDA/Window / pyautogui / Appium / Selenium. For **Playwright keep
the explicit form** `page = bot.click(page, ...)` so new-tab follows stay visible.

## AI-located actions (one model call each)

```python
bot.click(target, locate, *, modifier="", timeout=0.0, interval=2.0, wait="", thinking_level="", language="")
bot.type_text(target, locate, text, *, press_enter=False, clear_before_typing=False, timeout=0.0, ...)
bot.double_click(target, locate, ...)
bot.long_press(target, locate, *, duration=2.0, timeout=0.0, ...)  # mobile only
```

- `locate` is a natural-language description ("the blue Submit button").
- `modifier` (desktop only: pyautogui / the Windows window backend) holds modifier key(s)
  around the click — `"alt"`, `"ctrl+shift"`, etc. (`alt|ctrl|shift|win`, join
  with `+`). Atomic alt+click for games, ctrl+click multi-select. Other
  backends degrade to a plain click.
- `timeout>0` auto-waits (polls a visual assertion) until present, else raises
  `QirabotTimeoutError`. `wait="..."` overrides the derived assertion.
- Return the current target; if a click opened a **new tab**, that tab is
  returned — reassign: `page = bot.click(page, ...)`.

## AI checks (cheap — use even after `bot.ai`)

```python
text = bot.extract(target, instruction, *, language="")   # -> str
ok   = bot.verify(target, assertion)                       # -> bool, never raises
bot.wait_for(target, assertion, timeout=30.0, interval=2.0)# gate, raises on timeout
```

Honesty note: `verify`/`wait_for` poll an assertion (truthful). Prefer
`wait_for(assertion)` + `click(...)` over relying on a click to "find" something
that may not exist.

`extract()` reads what's **visible on screen** — it cannot return values that
live only in the DOM/attributes (a link's `href`, a `data-*`, an `input`'s real
value). For those, drop to the live driver via `bot.current_page(target)` (a real
Playwright `Page`, even with the explicit-target form) and read it deterministically:

```python
pg = bot.current_page(page)
urls = pg.eval_on_selector_all("a[href*='/video/']", "els => els.map(e => e.href)")
```

Also beware **ambiguous locates**: `extract(target, "the logged-in username")`
can grab a rotating search-box hint instead of the real value — scope the phrase
and cross-check against a screenshot.

**Human-in-the-loop waits** (QR scan, OTP, captcha — possibly minutes): each
`wait_for`/`verify` poll is a full model call, so the default `interval=2.0`
burns tokens while a human acts. Raise the interval (e.g. `interval=8.0`) or
skip the AI and poll the live driver for free, e.g.
`bot.current_page(target).wait_for_url("**/success**")` or watch
`...context.cookies()`. **Inside `bot.ai`**, register a `custom_tools` function
that blocks on `input()` until the human is done — zero model calls while
waiting, and the model resumes with the tool's return value (see `custom_tools`
above; the instruction must tell the model when to call it).

## Non-AI actions (no model call)

```python
page = bot.open(url="", headless=False, *, viewport=(1280,800), user_data_dir="", channel="", cdp_url="")
bot.navigate(target, "example.com")     # scheme optional
bot.go_back(target)                     # Playwright: smart (closes a historyless new tab)
page = bot.close_tab(page)              # Playwright only
bot.scroll(target, "down", 3)          # or distance=, x=, y=
page = bot.press_key(target, "Enter")  # "ctrl+c", "ctrl+t" (reassign on tab switch)
bot.press_key(target, "w", duration_seconds=2)  # desktop: hold 2s then release (0.1-10s, blocking)
bot.key_down(target, "w"); bot.key_up(target, "w")               # desktop: hold/release a key
bot.mouse_down(target, locate); bot.mouse_up(target, locate="")  # desktop: press-hold / release (mouse_down AI-located; locate=""=at cursor)
path = bot.screenshot(target)          # saved frame -> path (None if report=False)
bot.launch_app("WeChat")               # desktop: open an app (pyautogui can't)
```

### Persistent login — reuse a session across runs

A run is one session; login state is lost when it ends (see Lifecycle). To keep
cookies/login **between** runs, open with a persistent Chromium profile:

```python
import os
# Pass an ABSOLUTE path: qirabot forwards user_data_dir straight to Playwright's
# launch_persistent_context, and neither expands "~". A literal "~/..." would
# create a "./~/" dir in the CWD — always wrap with os.path.expanduser.
page = bot.open(url, user_data_dir=os.path.expanduser("~/.qira-profiles/<site>"))
if not bot.verify(page, "the user is logged in (avatar shown, no Login button)"):
    ...  # first run only: do the login (e.g. QR scan), then it sticks
```

The first run authenticates (scan / type credentials); later runs reuse the same
profile already logged in, so they skip straight to the task. This is the
intended pattern for any auth-gated automation.

## Platform support (summary)

**Frameworks → platforms** (the adapter is auto-detected from the object you
`bind()`, so "which framework" = which driver you build):

- Playwright / Selenium = browser.
- Appium = iOS / Android. On **iOS** Qirabot uses only screenshots + coordinates
  (no element finding), so Appium's job is just building WDA and forwarding. Once
  WebDriverAgent is running on :8100, reuse it via `webDriverAgentUrl` +
  `usePrebuiltWDA` to skip xcodebuild on every run (see `templates/ios_appium.py`).
  On a real device WDA must be built/signed once (Xcode or `appium driver run
  xcuitest open-wda`); a simulator lets Appium build it automatically. To skip
  the Appium server entirely and drive WDA over HTTP, use `templates/ios_wda.py`
  (qirabot's built-in WDA client) — same WDA, fewer moving parts.
- Built-in direct backends = Android (adb) / iOS (WDA) / Windows window — zero
  extra installs, spanning mobile and
  desktop. Its **desktop** backend (pywinauto) is **Windows-only (no macOS)**;
  reports as `desktop`, scopes to one window by HWND (`connect_device("Windows:///<hwnd>")`).
- pyautogui = desktop, **whole primary screen**, any OS (Win / macOS / Linux).

For Windows pick by scope: pyautogui for the whole screen (simplest),
`qirabot.Window` when you must isolate a single window. (Note:
`press_key` is less complete there than on pyautogui.)

**Windows: run elevated to drive elevated targets.** If the target app/game runs
as Administrator (common for games with anti-cheat), the script must also run as
Administrator — else Windows UIPI silently drops clicks/keystrokes (cursor moves,
nothing happens, no error). Rule: the script's privilege ≥ the target's.

AI-located actions (`click`/`type_text`/`double_click`) and AI ops
(`extract`/`verify`/`wait_for`/`ai`) work on **every** framework. Lower-level
actions vary:

- `navigate`/`close_tab`: browser only (`close_tab` = Playwright only).
- `go_back`: Playwright/Selenium/Appium/adb; WDA = edge swipe; pyautogui/Window = no.
- `long_press`: mobile only (Appium/adb/WDA).
- `press_key` on iOS: key names are mapped to characters (`enter`/`return`→`\n`,
  `tab`→`\t`); arrows/esc/home have no iOS soft-keyboard equivalent.
- `mouse_down`/`mouse_up`/`key_down`/`key_up`: desktop only (pyautogui + the Windows window backend); pair them — held input auto-released after `ai()`/`close()`. For a fixed-length hold prefer `press_key(target, key, duration_seconds=...)` (single blocking call, auto-release); keep `key_down`/`key_up` for holds that must span other actions. For a modifier+click prefer `click(target, locate, modifier="alt")` (atomic, millisecond-scale hold) over a `key_down`/`click`/`key_up` sequence — the sequence holds the modifier for seconds across decision steps, which flips UI state in many games.
- `right_click`/`hover`: full on browser/desktop; mobile taps / no-ops.

Unsupported actions raise `NotImplementedError`.

## Custom adapters (any framework qirabot doesn't ship)

For a backend outside the built-in list — airtest, cloud-device SDKs, a
custom engine bridge — subclass `qirabot.DeviceAdapter`. Required primitives:
`screenshot` / `click` / `double_click` / `type_text` / `press_key` /
`scroll` / `device_info`; everything else has sensible defaults. Two ways in:

```python
# A) pass an instance straight to bind() — no registration needed
bot = Qirabot().bind(MyAdapter(handle))

# B) register once; bind() then accepts the framework's native objects
from qirabot import register_adapter
register_adapter(MyAdapter)          # MyAdapter.accepts(target) decides matches
bot = Qirabot().bind(native_object)  # custom adapters are checked before built-ins
```

A complete reference implementation (airtest — all three 1.x target shapes,
cv2 screenshot encoding, Android keyevent mapping) lives at
`examples/airtest/adapter.py` in the qirabot-python repo: copy it into your
project; airtest stays your project's dependency, not qirabot's.

## Lifecycle

```python
with Qirabot(task_name="job") as bot:   # auto-close + report on exit
    ...
# or: bot.close()  (atexit also cleans up)
```

The `with` block auto-handles error states: an exception records the task as
**failed** (`fail()`); a `KeyboardInterrupt` records it as **cancelled**
(`cancel()`); normal exit records **success** via `close()`. Call these
manually when not using `with`:

```python
bot.fail("login wall hit — aborting")   # mark task failed (idempotent)
bot.cancel("user pressed Ctrl+C")       # mark task cancelled (idempotent)
bot.report()                            # write HTML report early (auto on close)
bot.close()                             # finalize — cannot override a prior fail/cancel
```

## Errors

```python
from qirabot import (QirabotError, AuthenticationError, QirabotTimeoutError,
                     ActionError, MissingDependencyError)
```

`QirabotError` is the base; catch it last. `AuthenticationError` means the
Google Cloud credentials (ADC) are missing or unusable — set
`GOOGLE_APPLICATION_CREDENTIALS` or run `gcloud auth application-default login`.
