"""Qirabot browser automation template — Qirabot launches its own Chromium.

Fill in the TODOs, then run:
    python -m venv .venv && source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
    uv pip install "qirabot[browser]" && playwright install chromium
    gcloud auth application-default login    # one-time Google Cloud auth (ADC)
    echo 'QIRA_MODEL=gemini-vertex/gemini-3.6-flash' > .env    # load_dotenv() reads this
    python browser.py

A self-contained HTML report (with screenshots) is written to
./qira_runs/<date>/<run>/report.html on close — open it to verify the run.
"""

from qirabot import Qirabot, StepResult, load_dotenv

# Read QIRA_MODEL (and other QIRA_* settings) from ./.env (a real exported env var wins).
load_dotenv()

# TODO: starting URL + the task to perform
START_URL = "https://www.saucedemo.com"
TASK = "Log in as standard_user / secret_sauce, then add the cheapest item to the cart"


def on_step(step: StepResult) -> None:
    # Print each step so the whole run is traceable from stdout — not just the
    # HTML report. Shows the model's per-step decision live, so a failed or
    # looping run is debuggable straight from the console.
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")


with Qirabot(task_name="browser-template") as bot:
    page = bot.open(START_URL, headless=False)

    # Default: hand the whole task to qirabot's agent loop (self-heals).
    result = bot.ai(page, TASK, max_steps=15, on_step=on_step)
    print("success:", result.success)
    print("output:", result.output)

    # If a script (CI gate, conditional flow) must branch on success,
    # uncomment. `verify` is an extra model call (token cost) — skip it when a
    # human will read the report:
    # if not bot.verify(page, "an item is in the shopping cart"):
    #     bot.fail("item never made it into the cart")

    # --- Optimization: for a stable flow you'll run repeatedly, hand-script the
    # --- steps instead (cheaper per action, deterministic, but brittle):
    # bot.type_text(page, "Username field", "standard_user")
    # bot.type_text(page, "Password field", "secret_sauce", press_enter=True)
    # bot.click(page, "Add to cart on the cheapest item")
