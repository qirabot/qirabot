"""Progress overlay: an on-screen status window the bot's screenshots can't see.

A small always-on-top window in the bottom-right corner shows what the bot is
doing. It is excluded from screen capture (macOS NSWindowSharingNone, Windows
WDA_EXCLUDEFROMCAPTURE) and click-through, so it neither appears in the bot's
own full-screen screenshots nor swallows a click aimed at the app below it.
macOS and Windows only; everywhere else it is a silent no-op.

Two ways to use it, shown below in one script:

1. `Qirabot(overlay=True)` — the bot drives the window: the instruction on
   ai() start, every step's action + reasoning, the final ✓/✗ outcome.
2. A standalone `Overlay` — your script drives the window, so phases that
   aren't bot steps (downloads, uploads, your own loops) show progress too;
   `ov.step` plugs the bot's steps in for just the AI part.

Install:
    python -m pip install "qirabot[desktop]"

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/desktop/overlay_progress.py
"""

import time

import pyautogui

from qirabot import Overlay, Qirabot

# --- 1. The bot drives the window: overlay=True and nothing else ----------

bot = Qirabot(task_name="overlay-auto", overlay=True)
result = bot.ai(pyautogui, "Open the system calculator and compute 6 * 7", max_steps=10)
print(f"Success: {result.success} — {result.output}")
bot.close()  # the window shows the ✓/✗ outcome for 1.5s, then disappears


# --- 2. Your script drives the window: phases beyond bot.ai() -------------

def my_on_step(step):
    print(f"  step {step.step}: {step.action_type}")


with Overlay() as ov:
    ov.begin("phase 1/2: pretending to fetch data…")
    time.sleep(2)  # stands in for your own non-bot work (API calls, files…)

    # edge_glow: the screen-edge "being controlled" breathing glow, for
    # phases that take over the real mouse/keyboard. The next begin() (or
    # finish()) without it turns the glow back off.
    ov.begin("phase 2/2: AI cleans up…", edge_glow=True)
    bot = Qirabot(task_name="overlay-manual")
    # ov.wrap() chains the window update in front of your own on_step;
    # use on_step=ov.step when you don't have a callback of your own.
    bot.ai(pyautogui, "Close the calculator", max_steps=6, on_step=ov.wrap(my_on_step))
    bot.close()
# leaving the `with` block closes the window
