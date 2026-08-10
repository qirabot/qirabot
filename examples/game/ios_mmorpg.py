"""Play a mobile MMORPG on a real iPhone — the script behind the
"zero to level 15" demo video at https://qirabot.com/docs/demos.html#mhxy_zero_to_15.

The whole "script" is one sentence: create a character and clear the
new-player flow. `bot.ai()` runs the full decision loop from there — look at
the screen, pick the next action, repeat — through dialogues, quests, and
battles. Pure vision: the game renders on the GPU, so there is no DOM or
accessibility tree to fall back on.

The built-in WDA backend talks HTTP straight to WebDriverAgent — no Appium
server, no extra Python packages.

Prerequisites:
    - WebDriverAgent running on the device and reachable at WDA_URL
      (verify: `curl http://127.0.0.1:8100/status`) — start it from Xcode
      (WebDriverAgentRunner test scheme) or `tidevice3 runwda`
    - USB device: forward both ports — `iproxy 8100 8100` and, for the
      recording, `iproxy 9100 9100` (WDA's MJPEG screen stream)
    - ffmpeg on PATH (recording only)
    - The game installed on the device and visible on screen

Install:
    uv pip install qirabot

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/game/ios_mmorpg.py

Environment variables:
    export WDA_URL=http://127.0.0.1:8100        # default
    export WDA_MJPEG_URL=http://127.0.0.1:9100  # default
    export IOS_BUNDLE_ID="com.netease.my"       # optional: launch the game first
                                                # (梦幻西游手游; another game, another id)
"""

import os

from qirabot import Qirabot, StepResult, WdaClient

# The task from the demo video, in the game's language. English: "This is
# Fantasy Westward Journey mobile. Create a character, then complete the
# new-player flow; skip whatever can be skipped."
TASK = "这是梦幻西游手游，你的任务是创建角色，然后完成新手流程，能跳过的尽可能跳过"

# The device is selected by its WDA URL: one `iproxy` per device on its own
# local port, then point WDA_URL at the one you want.
client = WdaClient(os.environ.get("WDA_URL", "http://127.0.0.1:8100"))

bundle_id = os.environ.get("IOS_BUNDLE_ID")
if bundle_id:
    client.app_launch(bundle_id)


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")


# language="zh" returns step decisions in Chinese to match the game; drop it
# for English. record_mjpeg_url records the *device* screen (the
# default recorder captures the host screen, which the phone isn't on);
# Qirabot starts it here, finalizes it on exit and embeds it in the report.
with Qirabot(task_name="mmorpg-new-player",
             language="zh",
             record_mjpeg_url=os.environ.get(
                 "WDA_MJPEG_URL", "http://127.0.0.1:9100")).bind(client) as bot:
    result = bot.ai(TASK, max_steps=200, on_step=on_step)
    print("\nResult:", result.output)
    print("Report:", bot.report_dir)
