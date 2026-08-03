"""Play a mobile MMORPG on a real iPhone — the script behind the
"zero to level 15" demo video at https://qirabot.com/#demos.

The whole "script" is one sentence: create a character and clear the
new-player flow. `bot.ai()` runs the full decision loop from there — look at
the screen, pick the next action, repeat — through dialogues, quests, and
battles. Pure vision: the game renders on the GPU, so there is no DOM or
accessibility tree to fall back on.

Prerequisites:
    - Appium server with the XCUITest driver (`appium driver install xcuitest`)
    - WebDriverAgent already running on the device and reachable at WDA_URL
      (verify: `curl http://127.0.0.1:8100/status`) — start it from Xcode
      (WebDriverAgentRunner test scheme) or `tidevice3 runwda`
    - The game installed on the device and visible on screen

Install:
    python -m pip install qirabot Appium-Python-Client

Run:
    # terminal 1
    appium --address 127.0.0.1 --port 4723

    # terminal 2
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    export IOS_UDID="00008030-..."      # `idevice_id -l`, or Xcode > Devices
    python examples/game/ios_appium_mmorpg.py

Environment variables:
    export IOS_DEVICE_NAME="iPhone"              # cosmetic label
    export WDA_URL=http://127.0.0.1:8100         # default
    export APPIUM_URL=http://127.0.0.1:4723      # default
"""

import base64
import os

from appium import webdriver
from appium.options.ios import XCUITestOptions

from qirabot import Qirabot, StepResult

# The task from the demo video, in the game's language. English: "This is
# Fantasy Westward Journey mobile. Create a character, then complete the
# new-player flow; skip whatever can be skipped."
TASK = "这是梦幻西游手游，你的任务是创建角色，然后完成新手流程，能跳过的尽可能跳过"

udid = os.environ.get("IOS_UDID")
if not udid:
    raise SystemExit("Set IOS_UDID to your device's UDID (`idevice_id -l`).")

options = XCUITestOptions()
options.platform_name = "iOS"
options.device_name = os.environ.get("IOS_DEVICE_NAME", "iPhone")
options.udid = udid
options.automation_name = "XCUITest"
options.no_reset = True
options.new_command_timeout = 300
# Reuse the WDA that is already running instead of letting Appium build and
# install its own — connecting drops from minutes to seconds.
options.set_capability("appium:webDriverAgentUrl",
                       os.environ.get("WDA_URL", "http://127.0.0.1:8100"))
options.set_capability("appium:usePrebuiltWDA", True)

driver = webdriver.Remote(
    os.environ.get("APPIUM_URL", "http://127.0.0.1:4723"), options=options
)


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")


try:
    # Game UIs push visual reasoning hard — pick a stronger model than the
    # default. language="zh" returns step decisions in Chinese to match the
    # game; drop it for English.
    with Qirabot(task_name="mmorpg-new-player",
                 model="gemini-vertex/gemini-3.6-pro",
                 language="zh").bind(driver) as bot:
        record_path = os.path.join(bot.report_dir, "recording.mp4")
        # iOS on-device recording: h264 mp4, 1800s max per segment.
        driver.start_recording_screen(
            videoType="h264", videoQuality="medium", timeLimit=1800
        )
        print(f"recording -> {record_path}")
        try:
            result = bot.ai(TASK, max_steps=200, on_step=on_step)
            print("\nResult:", result.output)
        finally:
            # Finalize the recording BEFORE leaving the with block so the
            # HTML report (generated in __exit__) can pick up the mp4.
            try:
                video_b64 = driver.stop_recording_screen()
                with open(record_path, "wb") as f:
                    f.write(base64.b64decode(video_b64))
                print(f"recording saved: {record_path}")
            except Exception as e:
                print(f"stop_recording failed: {e}")
finally:
    driver.quit()
