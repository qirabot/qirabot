"""Custom tools: let the AI call your GM backend mid-task.

Registers a gm_command tool with bot.ai(). When the model hits a blocker it
can solve with a GM command (out of energy, missing items), it calls the tool;
the SDK runs your function locally — your GM endpoint and token stay in your
script — and feeds the return value back so the model continues the UI flow.

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/game/custom_tool_gm.py
"""

import requests

from qirabot import AdbDevice, Qirabot

GM_URL = "http://internal-gm.example.com/exec"
GM_TOKEN = "replace-me"


def gm_command(command: str) -> str:
    """Send a command to the game's GM backend and return its reply.
    Available commands: add_energy <amount>, add_gold <amount>, finish_quest <quest_id>
    """
    resp = requests.post(GM_URL, json={"cmd": command}, headers={"X-GM-Token": GM_TOKEN}, timeout=10)
    return resp.text


bot = Qirabot(task_name="daily-quest-gm")
# First adb device; several devices -> AdbDevice(serial="emulator-5554"). A web
# or PC game works the same way — pass a Playwright page or desktop target instead.
device = AdbDevice()

result = bot.ai(
    device,
    "Open the daily quest screen and complete every daily quest. "
    "If an out-of-energy popup appears, use gm_command to add 100 energy and continue",
    max_steps=30,
    custom_tools=[gm_command],
    exclude_tools=["long_press"],  # optional: prune built-ins the task never needs
)

print(f"Success: {result.success}")
print(f"Output: {result.output}")
bot.close()
