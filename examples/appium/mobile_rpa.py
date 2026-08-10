"""Standalone mobile RPA: hand a whole task to the AI and let it drive the phone.

Unlike the pytest examples in this folder (which bolt AI onto your existing
mobile test suite), this is a plain script you run with `python`. There's no
`bot.open()` for mobile — you still build the Appium driver yourself — but from
there `bot.ai()` runs the full decision loop: it looks at the screen, picks the
next action, and repeats until the task is done or max_steps is hit.

Install:
    uv pip install qirabot Appium-Python-Client

Run (start the Appium server and a device first — see README.md):
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/appium/mobile_rpa.py

Environment variables:
    export APPIUM_URL=http://localhost:4723      # default
    export ANDROID_DEVICE=emulator-5554          # default
"""

import os
from appium import webdriver
from appium.options.android import UiAutomator2Options
from qirabot import Qirabot, StepResult

# The task to carry out, in plain language. Change this to whatever you need.
TASK = "Open Settings, go to About Phone, and report the Android version"

options = UiAutomator2Options()
options.platform_name = "Android"
options.device_name = os.environ.get("ANDROID_DEVICE", "emulator-5554")
options.app_package = "com.android.settings"
options.app_activity = ".Settings"

appium_url = os.environ.get("APPIUM_URL", "http://localhost:4723")
driver = webdriver.Remote(appium_url, options=options)


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params}")


# The with-block reports the task as failed (or cancelled on Ctrl+C) if anything
# raises, and completed otherwise — so the run report shows the right status.
try:
    with Qirabot(task_name="mobile-rpa").bind(driver) as bot:
        result = bot.ai(TASK, max_steps=12, on_step=on_step)

    print(f"\nSuccess: {result.success}")
    print(f"Output: {result.output}")
    print(f"Steps taken: {len(result.steps)}")
finally:
    # bot.close() only tears down browsers it launched; the Appium driver is
    # ours, so we quit it ourselves.
    driver.quit()
