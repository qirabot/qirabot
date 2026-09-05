# Standalone automation scripts

Plain Python scripts — run them with `python`, not `pytest`. No test framework,
no fixtures, no webdriver setup: `bot.open()` launches a Playwright-driven
Chromium for you and hands back a page.

Use these when you're **building automation** (scraping, RPA, an agent), not
augmenting a test suite. For the "bolt AI onto my existing tests" style, see the
[playwright](../playwright/), [selenium](../selenium/), [appium](../appium/), and
[desktop](../desktop/) examples instead.

## Install

```bash
uv pip install "qirabot[browser]"
playwright install chromium
gcloud auth application-default login   # auth: Google Cloud ADC, once
```

Pick a model via `QIRA_MODEL` or `Qirabot(model=...)`; the default is
`gemini-vertex/gemini-3.8-flash`.

## Run

```bash
python examples/automation/01_quickstart.py
```

## Examples

- [01_quickstart.py](01_quickstart.py) — `bot.open()` + click / type / extract / verify
- [02_multi_step_ai.py](02_multi_step_ai.py) — hand a whole task to `bot.ai()` with an `on_step` callback
- [03_scrape_data.py](03_scrape_data.py) — loop over URLs, extract a field from each, save JSON (headless)
- [04_connect_cdp.py](04_connect_cdp.py) — drive a Chrome you already have open via CDP
- [05_concurrent.py](05_concurrent.py) — run several browsers in parallel with a process pool, cloning a logged-in profile per worker
- [06_human_in_the_loop.py](06_human_in_the_loop.py) — a `custom_tools` function that pauses `bot.ai()` so a human can solve a CAPTCHA / login wall, then resumes
