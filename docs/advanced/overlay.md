---
title: Progress Overlay & ESC Kill Switch
description: The always-on-top progress window, the amber edge glow that marks real mouse/keyboard takeover, and the hold-ESC kill switch - capture-excluded, click-through, and safe to leave on everywhere.
---

# Progress Overlay & Kill Switch

Every CLI task command shows a small always-on-top window in the screen's
bottom-right corner: the running instruction, each step's action and
reasoning, and the final ✓/✗ outcome. Turn it off with `--no-overlay`; in the
SDK it's `Qirabot(overlay=True)` (off by default).

The window is built so it can never interfere with the run:

- **Excluded from screen capture** (macOS `NSWindowSharingNone`, Windows
  `WDA_EXCLUDEFROMCAPTURE`), so it never appears in the bot's own
  screenshots and never confuses the model.
- **Click-through**, so it never intercepts a click meant for the app below.
- Every failure is a silent no-op: on platforms without GUI support
  (Linux, CI, missing display) the overlay simply does nothing.

## Edge glow: the "hands off" signal

When a task drives the machine's real mouse and keyboard (the desktop
backends: `Window`, pyautogui), a slow-breathing amber glow lines the screen
edges for the duration of the run. It follows the same visual language as a
screen-sharing border: the machine is being controlled, so keep your hands
off. Remote-protocol targets (browser, Android, iOS) don't light it; your
mouse stays yours there.

The glow is capture-excluded like the window, with one stricter rule: on
Windows versions where exclusion isn't available it never shows at all,
because glowing bars in every screenshot would blind the bot.

The glow is not limited to `bot.ai()`: single-step calls (`bot.click`,
`bot.press_key`, `bot.type_text`, …) on desktop backends inject real input
too, so they light it as well. It comes on with the first call and fades a few seconds
after the last, so a scripted burst reads as one controlled stretch rather
than a flicker.

## Hold ESC to abort

While the glow is on, a small pill at the top of the screen reads
"Hold ESC to stop · 长按 ESC 中止". Hold ESC for about a second to abort
the run:

- The bot stops at the next step boundary. A step may take a few seconds to
  finish (an in-flight model call has to return), but no further input is
  injected while it does.
- Every key and mouse button the bot was holding is released.
- `bot.ai()` raises a `user_abort` error, and the task is recorded as
  cancelled rather than failed, so deliberate aborts stay out of your
  failure metrics.

Short ESC taps, whether yours or the bot's own, never trigger it.

The abort is sticky: every later `bot.ai()` on the same client raises
`user_abort` immediately, without lighting the glow or injecting input, so
a `try/except` around one run can't re-take the machine you just
reclaimed. Continuing requires an
explicit `bot.clear_user_abort()`. Single-step calls stay available for
cleanup.

The kill switch rides the overlay: `--no-overlay` (or `overlay=False`) turns
it off too. On the pyautogui backend there is a fallback that needs no
overlay at all: slam the mouse into any screen corner and leave it there
(pyautogui's built-in failsafe).

On macOS the ESC listener needs the Accessibility permission, the same one
desktop control already requires. Without it, the listener silently degrades
and the corner failsafe remains.

## SDK: automatic window

One flag covers the common case: the bot runs the window for you. It shows
the instruction as the headline with a running-state dot and elapsed
clock, each step as `step 3/20 · click · "…"` plus the model's reasoning,
and the final ✓/✗ outcome.

```python
bot = Qirabot(overlay=True)   # every bot.ai() run reports to the window
```

## SDK: standalone `Overlay`

When your script is more than one `bot.ai()` call, hold the window
yourself. A standalone `Overlay` displays whatever you tell it, whenever
you tell it, including your own phases; `ov.step` feeds it bot steps for
just the AI part:

```python
from qirabot import Overlay

with Overlay() as ov:
    ov.begin("phase 1/3: downloading data…")
    data = download_from_api()                    # your own code, no bot

    ov.begin("phase 2/3: filling in the report system…",
             edge_glow=True)                      # real mouse/keyboard ahead
    bot.ai(pyautogui, "Import the data into the report system",
           on_step=ov.step)                       # bot steps go to the window

    ov.begin("phase 3/3: sending the summary mail…")
    send_email(data)
```

If you already have an `on_step` callback of your own,
`on_step=ov.wrap(my_cb)` chains both.

Runnable example:
[overlay_progress.py](https://github.com/qirabot/qirabot/blob/main/examples/desktop/overlay_progress.py).

## Platform notes & known limits

- **macOS**: support installs automatically with qirabot (pyobjc).
- **Windows**: uses the standard library's tkinter. Full capture exclusion
  needs Windows 10 2004+; older versions show a black box in captures
  instead of the window content (the edge glow, per the stricter rule above,
  simply doesn't show there).
- **Everywhere else** (Linux, CI, missing GUI): the overlay is a silent
  no-op, so it can never break a run.
- **Exclusive-fullscreen games** bypass the desktop compositor, so no
  topmost window (overlay or glow) is visible above them. The run itself
  is unaffected; use borderless/windowed mode to see the overlay.
- After an ESC abort, a residual delay before the bot fully stops is the
  in-flight model call finishing; no input is injected during it.
