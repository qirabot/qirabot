"""Human-in-the-loop: pause bot.ai() for CAPTCHAs and login walls via a custom tool.

Registers a request_human_help tool with bot.ai(). When the model hits a
verification automation can't pass — a sign-in challenge, CAPTCHA, or 2FA
prompt — it calls the tool; the SDK runs it locally, which blocks on input()
until you finish the verification in the (headed) browser window, then the
model re-checks the page and continues the task.

A persistent profile (user_data_dir) makes the pause a one-time cost: the
session cookie survives, so the next run sails through without help. To skip
even the first pause, log in ahead of time with no AI task at all:
`qirabot open-browser --user-data-dir ~/.qirabot-profile` — this example is
for walls the model hits mid-task.

Install:
    uv pip install "qirabot[browser]"
    playwright install chromium

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/automation/06_human_in_the_loop.py
"""

import os

from qirabot import Qirabot, StepResult


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params}")


def request_human_help(message: str) -> str:
    """Call this tool when the page shows a verification that requires a human:
    sign-in challenge, CAPTCHA / anti-robot check, two-factor prompt, or any
    authentication wall that automation cannot pass. The task pauses until the
    human finishes the verification in the browser, then continues.
    """
    print(f"\n!! Human needed: {message}")
    input("Complete the verification in the browser window, then press Enter...")
    return "Human has completed the verification. Re-check the current page and continue the task."


bot = Qirabot(task_name="human-in-the-loop")
print(f"Task ID: {bot.task_id}")

# Headed + persistent profile: you can interact with the window when asked,
# and whatever you solve (login, cookie consent) sticks for the next run.
page = bot.open(
    "https://bing.com",
    headless=False,
    user_data_dir=os.path.expanduser("~/.qirabot-profile"),
    viewport=(1280, 960),
)

result = bot.ai(
    page,
    "Look up next week's weather in Shanghai. If the page shows a verification "
    "that needs a human (sign-in challenge, CAPTCHA), call request_human_help "
    "and continue after the human is done",
    max_steps=10,
    on_step=on_step,
    custom_tools=[request_human_help],
)

print(f"\nSuccess: {result.success}")
print(f"Output: {result.output}")

bot.close()
