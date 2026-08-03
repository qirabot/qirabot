---
title: AI Browser Automation in Python (Playwright)
description: Automate Chrome with AI vision instead of CSS selectors — Qirabot launches Chromium for you or bolts onto your existing Playwright or Selenium session. Headless, persistent profiles, CDP attach.
---

# Browser Automation

Qirabot drives the browser through **pixels, not the DOM**. The AI reads the
rendered page like a person does, so it works where selector-based automation
breaks: canvas apps, cross-origin iframes, shadow DOM, aggressive A/B-tested
layouts, and pages that change faster than your test suite.

You can let Qirabot manage the browser, or bolt it onto the Playwright /
Selenium session you already have.

## Managed browser

Requires the `browser` extra: `pip install "qirabot[browser]"` then
`qirabot install-browser`.

The quickest way to see it run is the CLI — one command, no code:

```bash
qirabot browser "Open the top story and summarize the discussion" --url news.ycombinator.com
qirabot browser "..." --headless --viewport 1920x1080
qirabot browser "..." --user-data-dir ~/.qira-profile --channel chrome   # logins survive runs
qirabot browser "..." --cdp-url http://localhost:9222                    # attach to running Chrome
```

`--cdp-url` also works with remote pools like browserless.

### Log in once, reuse the session

For sites that need an account, set the login up by hand — no AI task, no
model calls, no tokens spent. `open-browser` opens the profile in a visible
browser; log in, close the window, and every later run that passes the same
`--user-data-dir` starts already signed in:

```bash
qirabot open-browser --user-data-dir ~/.qira-profile --url news.ycombinator.com/login
# log in in the window, then close it
qirabot browser "Open the top story and summarize the discussion" --user-data-dir ~/.qira-profile
```

A profile directory can't be shared by two browsers at once, so close the
login window before running tasks. For login walls the AI hits *mid-task*
(captchas, 2FA), see [human-in-the-loop](/advanced/ai-tasks#human-in-the-loop).

The same run through the SDK — `bot.open()` launches Chromium (Playwright
under the hood), you never write framework code:

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://news.ycombinator.com")

result = bot.ai(page, "Open the top story and summarize the discussion")
print(result.output)

bot.close()
```

## Bolt onto the session you already have

Already running a browser through your own framework? Skip `bot.open()` and
pass your own object as the target — or `bind()` it once to stop repeating it
(`bind()` is covered in
[Custom Adapters & Bolt-On](/backends/custom-adapters)):

- **Playwright** — pass your `page`; mix your selectors with AI steps freely.
  Full guide: [Playwright + Qirabot](/frameworks/playwright).
- **Selenium** — pass (or `bind()`) your `driver`; not an extra, bring your
  own (`pip install qirabot selenium`). Full guide:
  [Selenium + Qirabot](/frameworks/selenium).
- **pytest** — AI assertions and AI steps inside your existing suite, with
  fixtures and CI notes. Full guide: [pytest + Qirabot](/frameworks/pytest).

One gotcha worth knowing up front: a click can open a **new tab**, and the
returned page is the live one — keep the form `page = bot.click(page, ...)`.
Details and the smart `go_back` behavior are in the
[API reference](/reference/api#navigation-scrolling-keys-no-ai).

## Notes

- Headless detection: on a display-less box (no `DISPLAY`), `bot.open()` and
  the CLI automatically run headless, with a warning. `open-browser` is the
  exception — it errors out instead, since a browser nobody can see is useless
  for manual login.
- `close_tab` is Playwright-only; `navigate`, `go_back`, `press_key`
  (including `ctrl+w` to close the current tab — reassign the returned page),
  and `scroll` all work. See the full per-platform action matrix in the
  [API Reference](/reference/api#platform-support-matrix).
