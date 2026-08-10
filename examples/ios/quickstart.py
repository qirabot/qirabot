"""iOS quickstart — direct via WebDriverAgent, zero extra installs.

The built-in WDA backend talks HTTP to a WebDriverAgent already running on
the device. USB real device: forward the port first (`iproxy 8100 8100`);
WDA itself is started from Xcode (WebDriverAgentRunner test scheme) or
`tidevice3 runwda`. No Appium server, no facebook-wda.

Install:
    uv pip install qirabot

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/ios/quickstart.py
"""

from qirabot import Qirabot, StepResult, WdaClient

TASK = "Open Settings, go to General > About, and report the iOS version"

client = WdaClient("http://127.0.0.1:8100")  # another device: its WDA URL
client.app_launch("com.apple.Preferences")


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params}")


with Qirabot(task_name="ios-quickstart").bind(client) as bot:
    result = bot.ai(TASK, max_steps=15, on_step=on_step)
    print("success:", result.success)
    print("output:", result.output)
    print("report:", bot.report_dir)
