---
title: Add AI to Selenium Tests — Natural-Language Locators for WebDriver
description: Bolt AI vision onto an existing Selenium WebDriver session - describe elements in plain English instead of brittle XPath, extract data visually, and run autonomous multi-step tasks.
---

# Selenium + Qirabot

Selenium suites tend to accumulate XPath. New steps written with Qirabot
don't need it: pass your existing `driver`, describe elements in plain
English, and AI vision locates them on the rendered page. Existing tests
keep running unchanged.

```python
from selenium import webdriver
from qirabot import Qirabot

driver = webdriver.Chrome()
driver.get("https://www.wikipedia.org")
bot = Qirabot().bind(driver)   # bind once; the driver is stable for the session

summary = bot.extract("Get the first paragraph of the article")
print(summary)

bot.close()      # close the bot first (finishes recording/report), then the driver
driver.quit()
```

Selenium is not a package extra, so bring your own driver:

```bash
pip install qirabot selenium
```

## bind() is the natural fit

Unlike Playwright (where clicks can return new tabs), a Selenium `driver`
object is stable for the whole session, so `bind()` removes the repeated
first argument:

```python
bot = Qirabot().bind(driver)
bot.click("the Accept Cookies button")
bot.type_text("the search box", "playwright vs selenium", press_enter=True)
ok = bot.verify("search results are shown")
rows = bot.extract("the first 5 result titles as a JSON array")
```

Works as a context manager too:

```python
with Qirabot().bind(driver) as bot:
    result = bot.ai("Log in as demo@example.com / hunter2 and open Settings")
    assert result.success
```

## Mixing with your existing code

```python
# Old-style, stays as-is
driver.find_element(By.ID, "username").send_keys("test_user")

# New steps: no locator maintenance
bot.click("the Submit button")
assert bot.verify("a green success banner is visible")
```

`go_back` maps to history-back; `navigate(driver, "example.com")` prepends
`https://` when missing. Tab management (`close_tab`) is Playwright-only,
so on Selenium manage windows natively.

## When Qirabot helps most in a Selenium suite

- Assertions about rendered state (`verify`) where DOM checks lie: the
  element is present but invisible, overlapped, or off-screen.
- Pages you don't own (payment iframes, SSO screens, captchas-adjacent
  flows) where selectors change under you.
- One-off data pulls (`extract`) that would otherwise mean a parsing
  function per page.

Related: [Browser backend](/backends/browser) ·
[pytest integration](/frameworks/pytest)
