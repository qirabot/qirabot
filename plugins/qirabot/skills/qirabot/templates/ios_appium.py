"""Qirabot iOS automation template (Appium XCUITest, reusing a running WDA).

Qirabot drives iOS purely by vision (screenshot + coordinates), so it never
needs Appium's element finding — only screenshots, taps, and key input, all of
which WebDriverAgent (WDA) already provides. The fastest, most reliable setup on
a real device is therefore: build/sign WDA ONCE, leave it running on :8100, and
let Appium reuse it (no xcodebuild per run). That's what this template does.

Prereqs (real device):
    1. WDA already running and reachable — verify it returns "ready":
           curl http://localhost:8100/status
       (On a fresh machine, build/sign WDA once via Xcode or
        `appium driver run xcuitest open-wda`, then keep it running. USB
        forwarding to :8100 is typically `iproxy 8100 8100` in another terminal.)
    2. Appium server up in another terminal:
           appium --address 127.0.0.1 --port 4723
    3. Run THIS script with the interpreter preflight echoed (not a bare
       `python`):  python scripts/preflight.py ios

Install:
    python -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
    uv pip install "qirabot[appium]"
    gcloud auth application-default login    # one-time Google Cloud auth (ADC)
    echo 'QIRA_MODEL=gemini-vertex/gemini-3.6-flash' > .env    # load_dotenv() reads this

When to pick this over ios_wda.py:
    - Simulators (Appium can build WDA itself — see Variant A below).
    - No pre-running WDA: let Appium build/sign on every run (Variant B).
    - You want Appium's first-party device APIs (screen recording, file
      transfer, deep XCUITest features).
    Pick ios_wda.py instead if WDA is already on :8100 and you want the
    minimal-deps path (no `appium` server, no XCUITest driver).

The HTML report is written to ./qira_runs/<date>/<run>/report.html on close.
"""

from appium import webdriver
from appium.options.ios import XCUITestOptions

from qirabot import Qirabot, StepResult, load_dotenv

# Read QIRA_MODEL (and other QIRA_* settings) from ./.env (a real exported env var wins).
load_dotenv()

# TODO: fill in your device + app, and the task.
UDID = "<your-device-udid>"           # `idevice_id -l`, or Xcode › Window › Devices
DEVICE_NAME = "<your-iphone-name>"    # Xcode › Devices (any non-empty label works)
BUNDLE_ID = "com.apple.Preferences"   # the app to drive; Settings as a safe default
WDA_URL = "http://127.0.0.1:8100"     # the already-running WDA
TASK = "Open General and show the software version"

# --- You build the Appium driver; bind() it once, then drive by language ------
options = XCUITestOptions()
options.platform_name = "iOS"
options.device_name = DEVICE_NAME
options.udid = UDID                    # required for a real device (omit on a simulator)
options.bundle_id = BUNDLE_ID
options.automation_name = "XCUITest"
options.no_reset = True                # keep app state / login between runs
options.new_command_timeout = 300
# Reuse the running WDA — skips xcodebuild build/sign on every run.
options.set_capability("appium:webDriverAgentUrl", WDA_URL)
options.set_capability("appium:usePrebuiltWDA", True)

driver = webdriver.Remote("http://127.0.0.1:4723", options=options)


def on_step(step: StepResult) -> None:
    # Print each step so the whole run is traceable from stdout — not just the
    # HTML report. Shows the model's per-step decision live, so a failed or
    # looping run is debuggable straight from the console.
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")


# bind() once: the driver is stable for the whole session, so every call drops
# the repeated first arg — bot.ai("...") instead of bot.ai(driver, "...").
with Qirabot(task_name="ios-template").bind(driver) as bot:
    # Default: hand the whole task to qirabot's agent loop (self-heals).
    result = bot.ai(TASK, max_steps=20, on_step=on_step)
    print("success:", result.success)
    print("output:", result.output)

    # Confirm the outcome (cheap check).
    # ok = bot.verify("the software version is shown")

driver.quit()


# --- Variant A: iOS Simulator (simplest — Appium builds WDA itself) -----------
# No udid, no webDriverAgentUrl; point at a booted simulator by name/version.
# options = XCUITestOptions()
# options.platform_name = "iOS"
# options.device_name = "iPhone 15"
# options.platform_version = "17.5"
# options.bundle_id = "com.apple.Preferences"
# options.automation_name = "XCUITest"
# driver = webdriver.Remote("http://127.0.0.1:4723", options=options)


# --- Variant B: real device WITHOUT a pre-running WDA -------------------------
# Let Appium build & sign WDA for you (iOS 17+ needs signing). Slower (xcodebuild
# every run) but no manual WDA step. Drop webDriverAgentUrl/usePrebuiltWDA above
# and add your Apple Developer team id:
# options.set_capability("appium:xcodeOrgId", "<your-team-id>")   # 10-char team id
# options.set_capability("appium:allowProvisioningDeviceRegistration", True)


# --- Optional: record the DEVICE screen into the report -----------------------
# record=True records the HOST screen, where the phone won't appear. Record the
# device via Appium instead and write it to bot.report_dir/recording.mp4 — the
# report auto-embeds any recording.mp4 there. Stop BEFORE the `with` exits
# (close() scans for it):
#
# import base64, os
# with Qirabot(task_name="ios-template").bind(driver) as bot:
#     driver.start_recording_screen()
#     try:
#         result = bot.ai(TASK, max_steps=20, on_step=on_step)
#     finally:
#         mp4 = base64.b64decode(driver.stop_recording_screen())
#         open(os.path.join(bot.report_dir, "recording.mp4"), "wb").write(mp4)
# driver.quit()
