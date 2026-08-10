"""Android quickstart — direct over adb, zero Python dependencies.

The built-in adb backend needs nothing but the adb binary (Android
platform-tools) on PATH and a device with USB debugging enabled.

Install:
    uv pip install qirabot

Run (connect a device/emulator first — check with `adb devices`):
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/adb/quickstart.py
"""

from qirabot import AdbDevice, Qirabot, StepResult

TASK = "Go to About Phone in Settings and report the Android version"

# No serial needed when exactly one device is connected;
# several devices -> AdbDevice(serial="emulator-5554").
device = AdbDevice()


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params}")


# The with-block reports the task as failed (or cancelled on Ctrl+C) if
# anything raises, and completed otherwise. Chinese/emoji text input works out
# of the box: the bundled ADBKeyboard IME is installed on first use and your
# keyboard is restored afterwards.
with Qirabot(task_name="adb-quickstart").bind(device) as bot:
    result = bot.ai(TASK, max_steps=15, on_step=on_step)
    print("success:", result.success)
    print("output:", result.output)
    print("report:", bot.report_dir)
