"""Windows window quickstart — bind to one window, game-readable input.

The built-in Windows backend (stdlib ctypes, zero extra installs) drives a
single window: screenshots are its client area, clicks are window-relative,
and keys go out as DirectInput scancodes — the input level games poll, which
virtual-key automation often can't reach.

For whole-desktop automation on any OS use the pyautogui backend instead
(`pip install "qirabot[desktop]"`, pass the pyautogui module as the target).

Install:
    python -m pip install qirabot

Run (Windows, with the target app open; auth is Google Cloud ADC — run
`gcloud auth application-default login` once):
    python examples\\windows\\quickstart.py
"""

from qirabot import Qirabot, StepResult, Window

TASK = "Type 'hello from qirabot' into the editor"

# Regex against visible window titles; exactly one match required.
# Know the handle instead? Window(hwnd=0x12345).
window = Window(title_re="Notepad")


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params}")


with Qirabot(task_name="windows-quickstart").bind(window) as bot:
    result = bot.ai(TASK, max_steps=10, on_step=on_step)
    print("success:", result.success)
    print("output:", result.output)
    print("report:", bot.report_dir)
