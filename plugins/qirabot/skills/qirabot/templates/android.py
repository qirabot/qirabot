"""Qirabot Android automation template (direct adb — built in, no server).

Fill in APP and TASK, then run:
    python -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
    python -m pip install qirabot        # zero extra dependencies for Android
    gcloud auth application-default login    # one-time Google Cloud auth (ADC)
    echo 'QIRA_MODEL=gemini-vertex/gemini-3-flash-preview' > .env    # load_dotenv() reads this
    adb devices            # confirm a device/emulator is connected
    python android.py

Drives the *device* by natural language — no Template images, no selectors.
The only host requirement is the adb binary (Android platform-tools) on PATH.
The HTML report is written to ./qira_runs/<date>/<run>/report.html on close.
"""

from qirabot import AdbDevice, Qirabot, StepResult, load_dotenv

# Read QIRA_MODEL (and other QIRA_* settings) from ./.env (a real exported env var wins).
load_dotenv()

# TODO: the app under test and what you want done
APP = "com.android.settings"
TASK = "Open Display settings and turn on Dark theme"

# No serial needed with exactly one device; else AdbDevice(serial="emulator-5554").
device = AdbDevice()


def on_step(step: StepResult) -> None:
    # Print each step so the whole run is traceable from stdout — not just the
    # HTML report. Shows the model's per-step decision live, so a failed or
    # looping run is debuggable straight from the console.
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")


device.shell(f"monkey -p {APP} -c android.intent.category.LAUNCHER 1")  # launch
with Qirabot(task_name="android-template").bind(device) as bot:
    # Default: hand the whole task to qirabot's agent loop (self-heals).
    result = bot.ai(TASK, max_steps=20, on_step=on_step)
    print("success:", result.success)
    print("output:", result.output)

    # Confirm the outcome (cheap check).
    # ok = bot.verify("dark theme is enabled")

    # --- Optimization: for a stable flow you'll run repeatedly, hand-script the
    # --- steps instead (cheaper per action, deterministic, but brittle):
    # bot.click("the Display menu item")
    # bot.click("the Dark theme toggle")
device.shell(f"am force-stop {APP}")


# --- Optional: record the DEVICE screen into the report -----------------------
# The SDK's record=True records the HOST screen, where a remote/headless device
# won't appear. Pass record_device=True instead: qirabot runs adb screenrecord
# for the bound AdbDevice and embeds the video in the report automatically:
#
# with Qirabot(task_name="android-template", record=True, record_device=True).bind(device) as bot:
#     result = bot.ai(TASK, max_steps=20, on_step=on_step)
