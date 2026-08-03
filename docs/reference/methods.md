---
title: Method Reference — Signatures, Parameters & Returns
description: Full signature of every Qirabot method - click, type_text, extract, verify, locate, wait_for, ai, open, bind, press_key, scroll, recording and lifecycle calls, and the result objects with token usage fields.
---

# Method Reference

Every public method on `Qirabot`, with its full signature. Constructor
options live in [Configuration](/advanced/configuration); how each underlying
action behaves per platform is the
[platform support matrix](/reference/api#platform-support-matrix).

Two notes up front:

- **`target`** is always the first parameter: the page from `bot.open()`, your
  own Playwright `page` / Selenium / Appium `driver`, an
  `AdbDevice` / `WdaClient` / `Window`, or the `pyautogui` module. On a
  [bound bot](/backends/custom-adapters#bind-—-drop-the-repeated-argument) it
  disappears from every call.
- Actions like `right_click`, `hover`, `clear_text`, and `drag` appear in the
  platform matrix but are **not** direct `bot.*` methods — they are tools the
  model uses inside [`ai()`](#ai) runs.

## Common parameters

The AI-located actions and AI operations share these keyword parameters —
documented once here:

| Parameter | Default | Meaning |
|---|---|---|
| `timeout` | `0.0` | Auto-wait: poll until the element looks present, up to this many seconds, before acting; `0` acts immediately. Raises `QirabotTimeoutError` on expiry. |
| `interval` | `2.0` | Seconds between auto-wait polls. |
| `wait` | `""` | Override the auto-derived presence assertion used by `timeout`. |
| `retry` | constructor's `retry` | Per-call override of the transient-failure retry count. |
| `thinking_level` | constructor's | Per-call [thinking level](/advanced/configuration#thinking-level) override: `minimal` / `low` / `medium` / `high`; empty = the engine default. |
| `language` | constructor's | Per-call response-language override. |

## Session & lifecycle

### bind()

```python
bind(target) -> bound bot
```

Fixes the target once; every method below then drops its first argument.
`with Qirabot().bind(driver) as bot:` works too. See
[Custom Adapters & Bolt-On](/backends/custom-adapters).

### open()

```python
open(url="", headless=False, *, viewport=(1280, 800), user_data_dir="",
     channel="", args=None, cdp_url="") -> page
```

Launches Chromium (requires `qirabot[browser]`) and returns the Playwright
page. `channel` uses an installed browser (`"chrome"`, `"msedge"`);
`user_data_dir` keeps a persistent profile (`~` expands to the home
directory on all platforms); `args` is a list of extra
Chromium flags; `cdp_url` attaches to a running Chrome instead of launching
(mutually exclusive with the launch options). On a display-less machine it
falls back to headless with a warning. See [Browser](/backends/browser).

### current_page()

```python
current_page(target) -> page
```

The live page/target — may differ from the original after a click opened a
new tab. Mostly useful on a bound bot, where you don't see returned pages.

### close()

```python
close() -> None
```

Releases held inputs, stops recording, writes the
[HTML report](/advanced/reports), closes what `open()` launched, and
records the run as complete. Auto-called by `atexit` and on context-manager
exit. Never closes a browser/driver you created yourself.

### fail() / cancel()

```python
fail(error_message="") -> None
cancel(reason="") -> None
```

Record a terminal outcome other than the success-complete that `close()`
records by default: `fail()` marks the run failed, `cancel()` marks a
deliberate abort — both reflected in the HTML report. Call before
`close()`.

### report_dir / task_id

Properties: the per-run output directory
(`./qira_runs/<date>/<time-id>/`) and the local run id (of the form
`local-<8 hex chars>`).

### usage

Property: session-wide AI usage totals so far, as a frozen `SessionUsage`
snapshot (read again for updated numbers). Covers every AI call on the
client — `ai()` steps, AI-located actions (`click()`/`type_text()`/…) and
standalone `extract()`/`verify()`/`locate()`. A failed call's spend is
included too; `ai_steps` counts successful calls only.

| Field | Type | Description |
|---|---|---|
| `ai_steps` | `int` | Successful AI calls so far |
| `input_tokens` | `int` | Non-cached prompt tokens |
| `cache_read_tokens` / `cache_write_tokens` | `int` | Cached prompt portion (prompt caching is active for Claude models; Gemini caches implicitly) |
| `output_tokens` / `thinking_tokens` | `int` | Output already includes thinking (Anthropic semantics; the Gemini provider normalizes to match). Anthropic reports no separate thinking count, so `thinking_tokens` stays 0 there |
| `total_tokens` | `int` | `input + cache read/write + output` — thinking not added again |
| `step_duration_ms` / `llm_decision_duration_ms` | `int` | Accumulated timing |

The CLI prints this summary after every task, and the HTML report header
shows the same totals.

## AI-located actions

All return the **current target** — reassign on browsers, where a click can
open a new tab (`page = bot.click(page, ...)`). All take the
[common parameters](#common-parameters).

### click()

```python
click(target, locate, *, modifier="", timeout=0.0, interval=2.0, wait="",
      retry=None, thinking_level="", language="") -> target
```

`locate` is a natural-language element description (any language).
`modifier` holds modifier keys around the click — `"alt"`,
`"ctrl+shift"` — desktop backends only.

### double_click()

```python
double_click(target, locate, *, <common>) -> target
```

Two quick taps on touch platforms.

### type_text()

```python
type_text(target, locate, text, *, press_enter=False,
          clear_before_typing=False, <common>) -> target
```

Locates the field, focuses it, types `text` (Chinese/emoji included).
**Empty `locate` skips AI location** and types into whatever has keyboard
focus — no AI, no model call; `timeout`/`wait` are ignored in that mode.

### long_press()

```python
long_press(target, locate, *, duration=2.0, <common>) -> target
```

Touch platforms only (Android/iOS) — browser/desktop raise
`NotImplementedError`.

### mouse_down() / mouse_up()

```python
mouse_down(target, locate, *, <common>) -> target
mouse_up(target, locate="", *, <common>) -> target
```

Split press/release for press-and-hold drags — desktop backends only.
`mouse_up` with no `locate` releases at the current cursor position (no AI,
no model call). Anything still held is auto-released at the end of an
`ai()` run and on `close()`.

### key_down() / key_up()

```python
key_down(target, key) -> target
key_up(target, key) -> target
```

Hold a key across other actions (desktop backends only). No AI, no model
call.

## AI operations

### ai()

```python
ai(target, instruction, max_steps=20, *, on_step=None, thinking_level="",
   language="", custom_tools=None, exclude_tools=None, knowledge=None) -> RunResult
```

The autonomous loop: screenshot → decide → act, until done or `max_steps`.
`on_step` is called with a [`StepResult`](#stepresult) after each step.
`custom_tools` registers your Python functions as callable tools;
`exclude_tools` removes built-ins by action name — both detailed in
[AI Tasks & Custom Tools](/advanced/ai-tasks). `knowledge` mounts domain
reference material for the run (text, a `Path`, or a list of either; 32KB
total).

### extract()

```python
extract(target, instruction, *, retry=None, thinking_level="",
        language="") -> ExtractResult
```

Structured data straight off the screen. The return value
[is a `str` subclass](#extractresult) carrying token usage.

### verify()

```python
verify(target, assertion, *, retry=None, thinking_level="",
       language="") -> VerifyResult
```

Visual assertion. A failed check doesn't raise — the result
[is truthy/falsy](#verifyresult) with a `.reason`; transport/model-endpoint
errors still raise.

### locate()

```python
locate(target, locate, *, timeout=0.0, interval=2.0, wait="",
       retry=None, thinking_level="", language="") -> LocateResult
```

Resolves a natural-language element description to coordinates **without
acting** — nothing is clicked or typed. Returns a
[`LocateResult`](#locateresult) that unpacks as a tuple:

```python
x, y = bot.locate(page, "the OK button")
page.mouse.click(x, y)   # drive your own framework with the coordinates
```

Coordinates are in the **adapter's screenshot pixel space**: window-relative
client pixels on the Windows window backend, physical screen pixels on
pyautogui, device pixels on mobile — the same space the bot's own actions
use, and what you see in the report screenshots, but not necessarily
OS-global coordinates.

The locate itself is a single vision call (no LLM tokens). With
`timeout > 0` it auto-waits first, same semantics as `click()` — each poll
is an LLM verify call against your model endpoint.

::: warning Absent elements
The vision resolver returns coordinates even when the element is **not on
screen**, and those coordinates are unreliable. When presence isn't
guaranteed, pass `timeout=` or check with `verify()` / `wait_for()` first.
:::

### wait_for()

```python
wait_for(target, assertion, timeout=30.0, interval=2.0, *,
         thinking_level="", language="") -> None
```

Polls `verify` semantics every `interval` seconds; returns as soon as the
condition holds, raises `QirabotTimeoutError` at `timeout`. Each poll is a
verify call to your model endpoint — prefer it over sleeps for correctness,
and keep `interval` reasonable to limit token usage.

## Direct actions — no AI

### navigate() / go_back() / close_tab()

```python
navigate(target, url) -> target      # "https://" prepended when missing
go_back(target) -> target            # smart on Playwright: closes a history-less new tab
close_tab(target) -> target          # Playwright only
```

Per-platform availability is in the
[matrix](/reference/api#platform-support-matrix); the smart `go_back`
behavior is described in the
[API overview](/reference/api#navigation-scrolling-keys-no-ai).

### scroll()

```python
scroll(target, direction="down", distance=3, *, x=None, y=None) -> None
```

Scrolls at the viewport center, or at `(x, y)` when given.

### press_key()

```python
press_key(target, key, duration_seconds=0) -> target
```

One key name works everywhere — adb keycode on Android, DirectInput
scancode on the Windows window backend. Combos join with `+`
(`"ctrl+shift+t"`, desktop/browser only). `duration_seconds > 0` holds the
key(s) before releasing (capped at 10; pyautogui + Windows window backend
only). Key vocabulary: [API overview](/reference/api#navigation-scrolling-keys-no-ai).

### screenshot()

```python
screenshot(target) -> Path | None
```

Saves to `report_dir/screenshots/`, returns the path (`None` when
`report=False`).

### launch_app()

```python
launch_app(app, *, wait=2.0) -> None
```

Launch or activate a desktop application, then wait `wait` seconds for its
window. Also importable standalone: `from qirabot import launch_app`.
Per-OS mechanics: [API overview](/reference/api#launch-a-desktop-app-no-ai).

## Reports & recording

```python
report(path=None) -> Path | None     # write the HTML report now (auto on close)
start_recording(*, fps=None, target=None, window=None, audio=None) -> bool
stop_recording() -> str | None       # returns the saved path
```

Normally you don't call these — `record=True` / `record_device=True` /
`record_mjpeg_url=...` on the constructor handle recording, and `close()`
writes the report. Manual control and all knobs:
[Reports & Recording](/advanced/reports).

## Result objects

### RunResult

Returned by `ai()`.

| Field | Type | Meaning |
|---|---|---|
| `success` | `bool` | `True` iff `status == "completed"` |
| `status` | `str` | `"completed"` / `"goal_failed"` / `"max_steps"` / `"error"` — see [Error Handling](/advanced/error-handling) |
| `output` | `str` | The model's final answer / summary |
| `steps` | `list[StepResult]` | Every step taken |

### StepResult

One entry per `ai()` step; also what `on_step` receives.

| Field | Type | Meaning |
|---|---|---|
| `step` | `int` | 1-based step number |
| `action_type` | `str` | The action taken (`click`, `scroll`, a custom tool name, …) |
| `params` | `dict` | The action's parameters |
| `output` | `str` | Action result fed back to the model |
| `finished` | `bool` | `True` on the final step |
| `decision` | `str` | The model's reasoning for this step |
| `input_tokens` / `output_tokens` / `thinking_tokens` | `int` | Token usage for this step |
| `step_duration_ms` / `llm_decision_duration_ms` | `int` | Wall-clock timings |

### ExtractResult

Returned by `extract()` — a `str` subclass, so use it directly as the
extracted text. Extra fields: `input_tokens`, `output_tokens`,
`thinking_tokens`. `output_tokens` already includes `thinking_tokens`, so a
call's spend is `input_tokens + output_tokens`. Note: str operations that
build a new string (slicing, `.strip()`, concatenation) return a plain
`str` and drop the token fields — read them on the value `extract()`
returned.

### VerifyResult

Returned by `verify()` — truthy when the assertion holds, so it drops
straight into `assert` / `if`. Fields: `passed` (`bool`), `reason` (the
model's explanation — worth logging when an assertion fails unexpectedly),
and the same three token fields as `ExtractResult`.

### LocateResult

Returned by [`locate()`](#locate). Unpacks as a tuple:
`x, y = bot.locate(...)`.

| Field | Type | Meaning |
|---|---|---|
| `x` / `y` | `int` | Resolved coordinates, in the adapter's screenshot pixel space |
| `input_tokens` / `output_tokens` / `thinking_tokens` | `int` | LLM token usage — currently `0` (locate is a single vision call) |
