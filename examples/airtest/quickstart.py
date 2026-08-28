"""Drive airtest devices with qirabot.

Copy adapter.py (next to this file) into your project first — airtest is a
dependency of your project, not of qirabot.

Install (your project):
    uv pip install qirabot airtest

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/airtest/quickstart.py
"""

from airtest.core.api import connect_device

from qirabot import Qirabot, register_adapter

from adapter import AirtestAdapter  # the copied file, wherever you put it

# Once registered, bind() accepts airtest targets directly.
register_adapter(AirtestAdapter)

device = connect_device("Android:///")  # first adb device; or Android:///emulator-5554

with Qirabot().bind(device) as bot:
    result = bot.ai("Go to About Phone in Settings and report the Android version")
    print(result.output)
