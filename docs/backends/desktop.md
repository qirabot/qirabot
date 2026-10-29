---
title: Desktop Automation with AI Vision (pyautogui)
description: Automate any desktop app on macOS, Windows, or Linux with AI vision on top of pyautogui — launch apps, click by description, hold keys, and record the screen.
---

# Desktop (pyautogui)

The desktop backend drives the whole screen on macOS, Windows, or Linux
through pyautogui, with AI vision replacing pixel-hunting and template
images. Describe the element; the AI finds it on the screenshot and the
click lands there.

Requires the `desktop` extra: `uv pip install "qirabot[desktop]"`.

The quickest check is the CLI:

```bash
qirabot desktop "Create a new note titled Groceries" --app Notes
```

The same thing in Python:

```python
import pyautogui
from qirabot import Qirabot

bot = Qirabot(task_name="wechat")

bot.launch_app("WeChat")              # macOS app name (or bundle id)
bot.ai(pyautogui, "Send 'hello' to honey in WeChat")
bot.close()
```

The target here is the `pyautogui` module itself. On the desktop there is
no page or driver object, so passing the module is how you say "drive the
whole screen". (Every call takes a target this way; see
[Custom Adapters & Bolt-On](/backends/custom-adapters) for the full list of
accepted target types, and `bind()` to stop repeating it.)

## Launching apps

pyautogui can move the mouse but cannot open an application. `launch_app`
shells out to the OS so runs start from a known app:

```python
bot.launch_app("WeChat")             # macOS: app name or bundle id
# launch_app("notepad")              # Windows: exe path, registered name, or UWP AppUserModelID
# launch_app("/path/to/app", wait=3) # wait for the window to appear (default 2s)
```

Per-OS launch mechanics are in the
[API reference](/reference/api#launch-a-desktop-app-no-ai).

## Desktop-only input primitives

The desktop backends (pyautogui and the
[Windows window backend](/backends/windows-games)) support input shapes the
web/mobile targets don't:

```python
bot.press_key(pyautogui, "w", duration_seconds=2)     # hold a key
bot.click(pyautogui, "file row", modifier="ctrl+shift")  # modifier-click
bot.key_down(pyautogui, "shift")                      # split press/release
bot.mouse_down(pyautogui, "the slider handle")        # press-and-hold drags
bot.mouse_up(pyautogui)                               # release at current cursor
```

Held inputs are auto-released at the end of an `ai()` run and on `close()`.
Exact semantics of each primitive are in the
[platform support matrix](/reference/api#platform-support-matrix).

## Multiple displays

By default the backend drives the **primary** display, the one with the menu
bar on macOS or the primary monitor on Windows. To drive another one, list
the displays and pick one by number:

```bash
qirabot displays
# 1*  1920x1080  @ (0, 0)  DELL P3223QE  [primary]
# 2   1710x1112  @ (-1710, 0)  Built-in Retina Display

qirabot desktop "Is this a code editor?" --display 2
```

```python
bot = Qirabot(display=2)          # or export QIRA_DISPLAY=2
bot.ai(pyautogui, "...")
```

Everything follows the selected display: screenshots capture only that
display, coordinates stay relative to its screenshot (the adapter adds the
display's offset), and `record=True` and the progress overlay go there too.
Numbering is 1-based, and 1 is always the primary display.

- An explicit `display=` / `--display` is strict: an unknown number fails
  at construction, and a non-desktop target (browser, mobile, a Windows
  `Window`) raises instead of silently ignoring it. `--display` cannot be
  combined with `--window-title` / `--hwnd`.
- `QIRA_DISPLAY` is best-effort: a bad value logs a warning and falls back
  to the primary display, and non-desktop targets ignore it.
- macOS and Windows only; on Linux only display 1 exists.

## Screen recording

`Qirabot(record=True)` records the full screen with ffmpeg for the whole run
and embeds `recording.mp4` in the HTML report. It records the
[selected display](#multiple-displays) (the primary one by default). macOS:
grant the terminal/IDE "Screen Recording" permission. Recording is
best-effort: a missing ffmpeg produces a warning and never fails the task.

## When to use which desktop backend

| | pyautogui backend | [Window backend](/backends/windows-games) |
|---|---|---|
| OS | macOS / Windows / Linux | Windows only |
| Scope | whole screen | one window (title / HWND) |
| Input level | virtual keys | DirectInput scancodes (game-readable) |
| Install | `qirabot[desktop]` | built in |

As a rule of thumb, use the Window backend on Windows when automating a game,
or when you want coordinates and screenshots scoped to one window instead of
the whole screen — it still brings that window to the foreground to send
input, so it is not a way to work in the background. Use pyautogui for
everything else on the desktop.
