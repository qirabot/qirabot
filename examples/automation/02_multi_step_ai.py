"""Standalone automation: hand a whole task to the AI with bot.ai().

bot.ai() runs the full decision engine — the AI looks at the screen, decides the
next action, and repeats until the task is done or max_steps is hit. Pass an
on_step callback to watch each decision as it happens.

Install:
    uv pip install "qirabot[browser]"
    playwright install chromium

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/automation/02_multi_step_ai.py
"""

from qirabot import Qirabot, StepResult

bot = Qirabot(task_name="multi-step-ai")
page = bot.open("https://news.ycombinator.com", headless=False)


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params}")


result = bot.ai(
    page,
    "Find the top story, open it, and summarize what it's about in one sentence",
    max_steps=10,
    on_step=on_step,
)

print(f"\nSuccess: {result.success}")
print(f"Output: {result.output}")
print(f"Steps taken: {len(result.steps)}")

bot.close()
