# Desktop + Qirabot

Use Qirabot with pyautogui for OS-level automation — desktop apps, embedded browsers, or sites with anti-bot detection.

## Install

```bash
python -m pip install qirabot pyautogui pytest
gcloud auth application-default login   # auth: Google Cloud ADC, once
```

## Run

```bash
pytest examples/desktop/test_browser_automation.py
pytest examples/desktop/test_native_app.py
python examples/desktop/overlay_progress.py   # plain script: the progress overlay window
```

> Note: requires a display. Won't work in headless CI environments.

## How it works

```python
import pyautogui
import pytest
from qirabot import Qirabot

@pytest.fixture(scope="session")
def bot():
    # bind once; the target is fixed. Closed after the last test.
    with Qirabot(task_name="my-test").bind(pyautogui) as bot:
        yield bot

def test_open_app(bot):
    # Open the app first — pyautogui can't launch apps, so use launch_app
    # (macOS: app name/bundle id, Windows: exe/name/AUMID, Linux: executable).
    bot.launch_app("Google Chrome", wait=2)

    # Bolt-on: AI finds and clicks UI elements by description
    bot.click("Chrome icon in the taskbar")
    bot.wait_for("Browser window is visible", timeout=10.0)

    bot.click("Address bar")
    bot.type_text("Address bar", "https://example.com")
    pyautogui.press("enter")

    # Bolt-on: AI verifies what's on screen
    assert bot.verify("Example Domain page is displayed")

    # Bolt-on: AI extracts text from the screen
    heading = bot.extract("What is the main heading?")
    assert "Example" in heading
```

## When to use Desktop mode

- Native desktop apps (no web interface)
- Sites with anti-bot detection (Playwright/Selenium get blocked)
- Embedded browsers in desktop apps (Electron, etc.)
- Cross-app workflows (copy from browser, paste into Excel)

## Running on a dedicated machine

Desktop mode captures the *whole* screen and drives the *real* mouse, so running
it on your own laptop sends your editor to the AI and fights you for the cursor.
To write/test scripts locally but run them on a separate machine (e.g. a Windows
VM), see [../runner/](../runner/) — a tiny HTTP runner plus a dedicated-VM
deployment guide.

## Examples

- [test_browser_automation.py](test_browser_automation.py) — Control browser from OS level
- [test_native_app.py](test_native_app.py) — macOS Calculator via `bot.launch_app` (replace with your app)
