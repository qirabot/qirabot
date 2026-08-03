---
title: Add AI to Playwright Tests — Vision Assertions & Self-Healing Steps
description: Inject AI vision into an existing Playwright suite - natural-language locators, visual assertions with bot.verify(), data extraction, and autonomous bot.ai() steps alongside your selectors.
---

# Playwright + Qirabot

Keep your Playwright suite exactly as it is — selectors, fixtures, CI — and
add AI where selectors hurt: dynamic content, canvas, third-party widgets,
and assertions about *what the page looks like* rather than what the DOM
contains.

```python
from playwright.sync_api import sync_playwright
from qirabot import Qirabot

bot = Qirabot()

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("https://github.com/trending")

    # Your selectors and AI steps interleave freely
    repos = bot.extract(page, "Get the top 5 trending repo names")
    print(repos)

    browser.close()
bot.close()
```

No configuration: every Qirabot action takes the Playwright `page` as its
first argument.

## What each call family gives you

- **`bot.verify(page, "the cart shows 1 item")`** — replaces
  element-exists assertions with a visual one. Survives markup rewrites,
  copy tweaks, and CSS refactors.
- **`bot.extract(page, "the prices in the results list as a JSON array")`**
  — structured data straight off the rendered page, no parsing logic.
- **`bot.click(page, "the Login button")`** — natural-language locator when
  a stable selector doesn't exist.
- **`bot.ai(page, "complete checkout as John Doe, zip 10001")`** — hand a
  whole flaky flow to the AI, assert on `result.success`.

## New tabs: reassign the returned page

A click can open a new tab; `click` / `type_text` / `press_key` return the
page your next native call should use. Keep the explicit form with
Playwright (rather than `bind()`), so tab switches stay visible:

```python
page = bot.click(page, "Open the first video")   # may return a new tab
page.fill("#comment", "nice")                    # native call on the right page

for i in range(4):
    page = bot.click(page, f"open video {i + 1}")
    bot.screenshot(page)
    page = bot.go_back(page)   # smart: closes the history-less new tab, back to the list
```

Closing a tab with `bot.press_key(page, "ctrl+w")` switches the active tab
too — same rule, reassign. If you do use a bound bot, the live page is
available as `bot.current_page()`.

## Auto-wait

AI-located actions poll until the element looks present, then act:

```python
bot.click(page, "Login button", timeout=15.0, interval=2.0)
bot.wait_for(page, "the dashboard has finished loading", timeout=15.0)
```

Playwright's own auto-waiting continues to apply to your native calls;
Qirabot adds no settle delay on Playwright (it trusts the framework).

## Under the hood

The decision engine runs locally inside the SDK: screenshots go directly
from your machine to your configured model endpoint (Google Vertex AI) for
reasoning and element location, and actions execute locally through your
Playwright session. Your code, cookies, and credentials never leave the
machine — only screenshots and instruction text are sent to the model.

Related: [Browser backend](/backends/browser) (managed browser, CDP attach,
persistent profiles) · [pytest integration](/frameworks/pytest)
