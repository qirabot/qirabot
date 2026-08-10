---
title: Add AI to Playwright Tests — Vision Assertions & Self-Healing Steps
description: Inject AI vision into an existing Playwright suite - natural-language locators, visual assertions with bot.verify(), data extraction, and autonomous bot.ai() steps alongside your selectors.
---

# Playwright + Qirabot

You can keep your Playwright suite as it is: selectors, fixtures, and CI
all stay in place. Add AI where selectors hurt: dynamic content, canvas,
third-party widgets, and assertions about *what the page looks like* rather
than what the DOM contains.

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

There is no configuration step: every Qirabot action takes the Playwright
`page` as its first argument.

## What each call family gives you

- `bot.verify(page, "the cart shows 1 item")` replaces an element-exists
  assertion with a visual one. It survives markup rewrites, copy tweaks,
  and CSS refactors.
- `bot.extract(page, "the prices in the results list as a JSON array")`
  returns structured data from the rendered page, with no parsing logic
  on your side.
- `bot.click(page, "the Login button")` is a natural-language locator for
  when a stable selector doesn't exist.
- `bot.ai(page, "complete checkout as John Doe, zip 10001")` hands a whole
  flaky flow to the AI; assert on `result.success`.

## New tabs: reassign the returned page

A click can open a new tab. `click`, `type_text`, and `press_key` return
the page your next native call should use. With Playwright, keep the
explicit form (rather than `bind()`) so tab switches stay visible in your
code:

```python
page = bot.click(page, "Open the first video")   # may return a new tab
page.fill("#comment", "nice")                    # native call on the right page

for i in range(4):
    page = bot.click(page, f"open video {i + 1}")
    bot.screenshot(page)
    page = bot.go_back(page)   # smart: closes the history-less new tab, back to the list
```

Closing a tab with `bot.press_key(page, "ctrl+w")` also switches the
active tab, so the same rule applies: reassign. If you do use a bound bot,
the live page is available as `bot.current_page()`.

## Auto-wait

AI-located actions poll until the element looks present, then act:

```python
bot.click(page, "Login button", timeout=15.0, interval=2.0)
bot.wait_for(page, "the dashboard has finished loading", timeout=15.0)
```

Playwright's own auto-waiting still applies to your native calls. Qirabot
adds no settle delay of its own on Playwright; it trusts the framework's
waiting.

## Under the hood

The decision engine runs in your own process: screenshots go directly
from your machine to your configured model endpoint (Google Vertex AI) for
reasoning and element location, and actions execute locally through your
Playwright session. Only screenshots and instruction text are sent to the
model; your code, cookies, and credentials never leave the machine.

Related: [Browser backend](/backends/browser) (managed browser, CDP attach,
persistent profiles) · [pytest integration](/frameworks/pytest)
