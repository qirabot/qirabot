---
title: API Reference — Actions & Platform Support
description: Every Qirabot action - AI-located clicks and typing, extract/verify/wait_for, bot.ai(), direct navigation and key presses without a model call, the full per-platform support matrix, and the run lifecycle.
---

# API Reference

This page is a working overview: what each call does and how it maps per
platform. Full signatures, every keyword parameter, and the result objects
are in the [Method Reference](/reference/methods).

## Simple actions (AI-located)

These actions use vision-based element location. Each call is a single
request to your configured model:

```python
# Click on an element by description
bot.click(page, "Login button")

# Auto-wait: poll until the element looks present (up to timeout) before
# clicking, else raise QirabotTimeoutError. Works on every framework.
# `wait` overrides the auto-derived assertion. (Also on type_text/double_click.)
bot.click(page, "Login button", timeout=15.0, interval=2.0)

# Modifier-click: hold modifier key(s) around the click (desktop only)
bot.click(target, "enemy unit", modifier="alt")       # alt+click (games)
bot.click(target, "file row", modifier="ctrl+shift")  # join several with "+"

# Type text into an input field
bot.type_text(page, "Email input", "user@example.com")

# Extract data from the screen
text = bot.extract(page, "Get the main heading")

# Verify a visual assertion — a failed check doesn't raise, it returns a
# falsy VerifyResult (with a .reason); truthy when the check passes
ok = bot.verify(page, "The success message is visible")

# Wait for a condition (acts as a gate): returns when met, else raises
# QirabotTimeoutError. Use verify() for a non-raising bool check.
bot.wait_for(page, "Page has finished loading", timeout=15.0, interval=2.0)
```

`click`, `type_text`, and `double_click` return the current target (the same
kind you passed in). When an action opens a link in a new tab, the return
value is that new tab. Reassign it to keep operating on the active page:

```python
page = bot.click(page, "Open the first video")  # may switch to a new tab
```

## Multi-step AI — bot.ai()

```python
result = bot.ai(page, "Search for SpaceX and summarize the first result", max_steps=10)
print(result.success, result.status, result.output)
```

Step callbacks, `custom_tools`, and `exclude_tools` are covered in
[AI Tasks & Custom Tools](/advanced/ai-tasks); run outcomes in
[Error Handling](/advanced/error-handling).

## Navigation, scrolling & keys (no AI)

Direct actions that don't need AI element location. `go_back`, `navigate`,
`close_tab`, and `press_key` return the current page/target (may differ after
the action); `scroll` returns `None`.

```python
bot.navigate(page, "example.com")   # scheme optional; "https://" prepended
bot.go_back(page)                   # back to the previous page (smart, see below)
page = bot.close_tab(page)          # close current tab, return to previous tab
bot.scroll(page, "down", 3)         # scroll at viewport center
bot.scroll(page, "up", distance=5, x=640, y=400)  # scroll at a point
bot.press_key(page, "Enter")        # a single key
bot.press_key(page, "ctrl+c")       # a combo (join with "+")
bot.press_key(target, "w", duration_seconds=2)  # hold for 2s (desktop only)
page = bot.press_key(page, "ctrl+w")  # closes the tab, switches to another — reassign
bot.type_text(page, "", "hello", press_enter=True)  # empty locate: type into the
                                    # focused element directly (no AI, no model call)
```

**Direct typing.** `type_text` with an empty `locate` skips AI location
and types into whatever currently has keyboard focus. This is useful when
focus is already where you want it (a game chat box opened with Enter, a
field reached via Tab). Making sure focus is right is your responsibility;
`press_enter` / `clear_before_typing` still work, and `timeout`/`wait` are
ignored.

**`press_key` key names.** One name works on every backend; each backend
maps it to its own vocabulary:

| Category | Examples | Notes |
| --- | --- | --- |
| Single keys | `Enter` `Escape` `Tab` `Backspace` `Delete` `Space` | |
| Arrows / paging | `ArrowUp/Down/Left/Right` `PageUp` `PageDown` `Home` `End` | |
| Combos (desktop/browser) | `ctrl+c` `ctrl+a` `alt+tab` `ctrl+shift+t` | modifiers `ctrl` `alt` `shift` `cmd` (= meta/win); join with `+` |
| Mobile (Android/iOS) | `Back` `Home` `Menu` `Enter` | single keys only, no combos. `Back`/`Menu` are Android-only; iOS (WDA) supports `Home`, `Enter`, volume and lock keys and raises `NotImplementedError` for the rest |
| Hold (desktop) | `duration_seconds=2` (float > 0, capped at 10) | holds the key(s) that long before releasing, e.g. quantified in-game movement (`w`, `shift+w`). pyautogui + Windows window backend only; web/mobile ignore it and tap |

So `bot.press_key(t, "Enter")` becomes an adb keycode on Android and a
DirectInput scancode on the Windows window backend automatically.

**Smart `go_back` (Playwright):** if the current page has back history it
goes back in place. If it has none (for example, a click opened a link in a
new tab, which starts with no history) and another tab is open, it closes
the current tab and returns to the previous one:

```python
for i in range(4):
    page = bot.click(page, f"open video {i + 1}")  # opens a new tab
    bot.screenshot(page)
    page = bot.go_back(page)                       # closes it, back to the list
```

Use `close_tab` to force-close the current tab regardless of history.

## Platform support matrix

| Action         | Playwright | Selenium | Appium (mobile) | pyautogui (desktop) | adb (Android) | WDA (iOS) | Window (Windows) |
| -------------- | :--------: | :------: | :-------------: | :-----------------: | :-----------: | :-------: | :--------------: |
| `click`        |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |
| `double_click` |     ✅     |    ✅    |      ✅ ᵃ       |         ✅          |     ✅ ᵃ      |    ✅     |        ✅        |
| `right_click`  |     ✅     |    ✅    |    = tap ᵇ      |         ✅          |    = tap ᵇ    |  = tap ᵇ  |        ✅        |
| `hover`        |     ✅     |    ✅    |    no-op ᶜ      |         ✅          |    no-op ᶜ    |  no-op ᶜ  |        ✅        |
| `type_text`    |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |
| `clear_text`   |     ✅     |    ✅    |       ✅        |         ✅          |     ✅ ᵈ      |   ✅ ᵈ    |        ✅        |
| `press_key`    |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |       ✅ ᵉ       |
| `scroll`       |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |
| `drag`         |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |
| `long_press`   |     ❌ ᶠ    |    ❌ ᶠ   |       ✅        |         ❌ ᶠ         |      ✅       |    ✅     |       ❌ ᶠ        |
| `mouse_down`   |     ❌ ᵍ    |    ❌ ᵍ   |       ❌ ᵍ      |         ✅          |     ❌ ᵍ      |   ❌ ᵍ    |        ✅        |
| `mouse_up`     |     ❌ ᵍ    |    ❌ ᵍ   |       ❌ ᵍ      |         ✅          |     ❌ ᵍ      |   ❌ ᵍ    |        ✅        |
| `key_down`     |     ❌ ᵍ    |    ❌ ᵍ   |       ❌ ᵍ      |         ✅          |     ❌ ᵍ      |   ❌ ᵍ    |        ✅        |
| `key_up`       |     ❌ ᵍ    |    ❌ ᵍ   |       ❌ ᵍ      |         ✅          |     ❌ ᵍ      |   ❌ ᵍ    |        ✅        |
| `navigate`     |     ✅     |    ✅    |       ✅        |         ❌          |      ❌       |    ❌     |        ❌        |
| `go_back`      |     ✅     |    ✅    |       ✅        |         ❌          |      ✅       |   ✅ ʰ    |        ❌        |
| `close_tab`    |     ✅     |    ❌    |       ❌        |         ❌          |      ❌       |    ❌     |        ❌        |
| `screenshot`   |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |

AI-located actions (`click`, `type_text`, `double_click`) and the AI
operations (`extract`, `verify`, `wait_for`, `ai`) work on every framework;
the matrix shows how each underlying action maps per platform.

- ᵃ Touch platforms emulate `double_click` as two quick taps.
- ᵇ Mobile has no right-click: it degrades to a tap.
- ᶜ Touch targets have no hover: it's a no-op on mobile.
- ᵈ No element model over raw adb/WDA; `clear_text` is best-effort (caret-to-end + repeated delete on Android, backspace burst on iOS).
- ᵉ The Windows window backend sends DirectInput scancodes (real hardware-level keys, incl. `ctrl`/`alt`/`win` combos); characters outside the scancode table are injected as unicode key events. `duration_seconds` (hold) works on pyautogui + the Windows window backend only; elsewhere it degrades to an instant tap.
- ᶠ `long_press` is a touch-only gesture (Android/iOS). Browser/desktop adapters raise `NotImplementedError`.
- ᵍ `mouse_down`/`mouse_up`/`key_down`/`key_up` are desktop-only split press/release primitives (pyautogui + the Windows window backend) for holding an input across other actions. Pair each press with its release; any input still held is auto-released at the end of an `ai()` run and on `close()`. `mouse_up`'s locate is optional (omit it to release at the current cursor position, deterministically and without an AI call). Browser/mobile adapters raise `NotImplementedError`.
- ʰ iOS has no back button; `go_back` performs the universal left-edge swipe gesture.

`navigate`/`go_back` raise `NotImplementedError` where unsupported.
`close_tab` is Playwright-only, so the new-tab fallback inside `go_back`
applies to Playwright only: on Selenium/Appium `go_back` is always
history-back, and on Android it maps to `keyevent BACK`.

## Screenshot (no AI)

Saves to `report_dir/screenshots/` and returns the saved path (or `None`
when `report=False`):

```python
path = bot.screenshot(page)
```

## Launch a desktop app (no AI)

pyautogui can drive the mouse and keyboard but cannot open an application.
`launch_app` shells out to the OS so desktop runs start from a known app:

```python
bot.launch_app("WeChat")             # macOS app name (or bundle id)
# launch_app("notepad")              # Windows: exe path, registered name, or UWP AppUserModelID
# launch_app("/path/to/app", wait=3) # wait seconds for the window to appear (default 2)
```

On macOS it uses `open -a`/`open -b` (activating an already-running app), on
Windows `os.startfile`/`start`/`explorer.exe shell:AppsFolder`, on Linux the
executable directly. Also available standalone:
`from qirabot import launch_app`.

## Task lifecycle

Each `Qirabot` instance manages a local run that tracks all operations:
created on construction with a local run id (`bot.task_id`, of the form
`local-<8 hex chars>`), every `click()` / `extract()` / `ai()` recorded as
a step, marked complete on `close()` or context-manager exit. All run
bookkeeping stays on your machine and is used only to build the HTML report:

```python
with Qirabot(task_name="my automation") as bot:
    page = bot.open("https://example.com")
    print(bot.extract(page, "Get the main heading"))
# bot.close() is called automatically
```

If `close()` is never called, `atexit` cleans up on script exit.

Two more lifecycle calls record a terminal outcome other than
"completed": `bot.fail("what went wrong")` records the run as failed, and
`bot.cancel("why")` records it as cancelled. Call either before (or instead
of) the success-complete that `close()` records by default. The outcome
shows up in the run's HTML report.

See also: [Configuration](/advanced/configuration) (constructor options,
model selection, settle delay) ·
[Error Handling](/advanced/error-handling) ·
[Custom Adapters](/backends/custom-adapters) (`bind()`, `DeviceAdapter`)
