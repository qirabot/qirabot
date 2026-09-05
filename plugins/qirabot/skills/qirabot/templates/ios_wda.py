"""Qirabot iOS automation template (direct WDA — built in, no Appium server).

Uses qirabot's built-in WDA client, which talks straight to WebDriverAgent
(WDA) over HTTP. Fewer moving parts than ios_appium.py (no `appium` server
process, no XCUITest driver) and zero extra Python dependencies.

Zero go-ios/tunnel dependency: every action (screenshot, tap, type, app
launch) goes through WDA's HTTP API, so iOS 17+ devices do NOT need
`sudo ios tunnel start` or a RemoteXPC tunnel daemon.

Prereqs (real device):
    1. WDA built/signed once via Xcode and currently running on the device.
       (Same as ios_appium.py — qirabot can't drive iOS without WDA.)
    2. WDA reachable at http://127.0.0.1:8100. The standard USB path is iproxy
       in a separate terminal:
           iproxy 8100 8100        # brew install libimobiledevice
       Verify it returns "ready":
           curl http://127.0.0.1:8100/status

Install:
    python -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
    uv pip install qirabot       # the WDA client is built in
    gcloud auth application-default login    # one-time Google Cloud auth (ADC)
    echo 'QIRA_MODEL=gemini-vertex/gemini-3.8-flash' > .env    # load_dotenv() reads this

When to pick this over ios_appium.py:
    - You already have WDA running on :8100 and don't want an Appium server.
    - Faster cold-start (no XCUITest driver attach), nothing extra to install.
    Pick ios_appium.py instead for simulator support, auto WDA build/sign, or
    first-party Appium ecosystem features.

The HTML report is written to ./qira_runs/<date>/<run>/report.html on close.
"""

from qirabot import Qirabot, StepResult, WdaClient, load_dotenv

# Read QIRA_MODEL (and other QIRA_* settings) from ./.env (a real exported env var wins).
load_dotenv()

# TODO: the app under test and what you want done
WDA_URL = "http://127.0.0.1:8100"
BUNDLE_ID = "com.apple.Preferences"     # the app to drive; Settings as a safe default
TASK = "Open General and show the software version"

client = WdaClient(WDA_URL)


def on_step(step: StepResult) -> None:
    # Print each step so the whole run is traceable from stdout — not just the
    # HTML report. Shows the model's per-step decision live, so a failed or
    # looping run is debuggable straight from the console.
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")


# Launch the app by creating a WDA session bound to its bundle id — works on
# every iOS version with no extra setup.
client.app_launch(BUNDLE_ID)


with Qirabot(task_name="ios-wda-template").bind(client) as bot:
    # Default: hand the whole task to qirabot's agent loop (self-heals).
    result = bot.ai(TASK, max_steps=20, on_step=on_step)
    print("success:", result.success)
    print("output:", result.output)

    # Confirm the outcome (cheap check).
    # ok = bot.verify("the software version is shown")


# --- Note on device recording -------------------------------------------------
# To embed a device video in the report, pass record=True together with
# record_mjpeg_url="http://127.0.0.1:9100" (WDA's MJPEG stream; USB real device
# also needs `iproxy 9100 9100`, and ffmpeg must be installed) — or switch to
# ios_appium.py, which uses Appium's session recording API.
