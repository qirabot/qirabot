---
title: Quick Start
description: Run your first AI-driven GUI automation in one command, then the same task through the Python SDK — autonomous bot.ai() tasks and deterministic AI-located steps.
---

# Quick Start

The decision engine runs in your own process and calls a vision model on
your own Google Cloud Vertex AI endpoint. Authenticate to Google Cloud once,
then hand the AI a task:

```bash
gcloud auth application-default login   # once
qirabot browser "Search for SpaceX and get the first sentence of the article" --url wikipedia.org
```

The browser opens, the AI does the task, and the result — plus an HTML report —
lands in your terminal. The rest of the commands and options are in the
[CLI Reference](/guide/cli).

Runs default to `gemini-vertex/gemini-3.6-flash` on your credentials' own
project. To change the model or project, or to authenticate with an API key
instead of gcloud, see [Configuration](/advanced/configuration).

## The same task in Python

`bot.ai()` is the same engine the CLI command runs: the AI looks at the
screen, decides the next action, and loops until the task is done:

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://www.wikipedia.org")

result = bot.ai(page, "Search for SpaceX and get the first sentence of the article")
print(f"Success: {result.success}")
print(f"Result: {result.output}")

bot.close()
```

## Deterministic steps

When you want to drive each step yourself instead of delegating the whole
task, the same natural-language targeting is available as single-step calls.
These are fast, low-cost, and stay inside your own control flow:

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://www.saucedemo.com")

# Describe each element in natural language (any language works);
# AI vision locates it, your code stays in control:
bot.type_text(page, "the Username field", "standard_user")
bot.type_text(page, "the Password field", "secret_sauce")
bot.click(page, "the Login button")

# Gate on visual state — wait_for polls until true, raises on timeout
bot.wait_for(page, "the Products page is shown")

# Pull structured data straight off the screen — no scraping, no selectors
count = bot.extract(page, "the number on the cart badge as an integer")

bot.close()
```

The core calls:

| Call | What it does |
|---|---|
| `bot.ai(target, task)` | Autonomous multi-step task: see, decide, act, loop until done |
| `bot.click(target, "desc")` | AI-located click (also `double_click`, `type_text`) |
| `bot.extract(target, "desc")` | Pull structured data from the screen |
| `bot.verify(target, "assertion")` | Visual assertion: truthy/falsy result, a failed check doesn't raise |
| `bot.wait_for(target, "condition")` | Poll until a visual condition holds, else raise |

`target` is whatever surface you're driving: the page returned by
`bot.open()`, a Playwright/Selenium/Appium object of your own, or the
`pyautogui` module for the desktop. The full call list and per-platform
behavior is in the [API reference](/reference/api).

## How a run ends

`result.success` is the pass/fail verdict; `result.status` says why:
`"completed"`, `"goal_failed"` (login wall, captcha), `"max_steps"` (out of
step budget), or `"error"`. Details and the exception hierarchy are in
[Error Handling](/advanced/error-handling).

```python
result = bot.ai(page, "Find the cheapest flight and hold it")
if result.status == "max_steps":
    # not a real failure — the budget was too small; retry with headroom
    result = bot.ai(page, "Find the cheapest flight and hold it", max_steps=50)
```

## Reports

Every run writes a self-contained HTML report with per-step screenshots to
`./qira_runs/<date>/<time-id>/`, even on error or Ctrl+C, so you can see where
the run stopped. Pass `record=True` (`--record` on the CLI) to also capture a
video.

## Next steps

- Pick your backend: [Browser](/backends/browser) ·
  [Android](/backends/android) · [iOS](/backends/ios) ·
  [Windows & Games](/backends/windows-games) · [Desktop](/backends/desktop)
- If you're attaching to an existing Playwright / Selenium / Appium suite,
  see [Custom Adapters & Bolt-On](/backends/custom-adapters)
- [CLI Reference](/guide/cli)
