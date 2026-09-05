# Playwright + Qirabot

Use Qirabot as a bolt-on AI layer on top of your existing Playwright tests.

## Install

```bash
uv pip install qirabot pytest-playwright
playwright install chromium
gcloud auth application-default login   # auth: Google Cloud ADC, once
```

Pick a model via `QIRA_MODEL` or `Qirabot(model=...)` if you don't want the
default (`gemini-vertex/gemini-3.8-flash`).

## Run

```bash
# headless (default)
pytest examples/playwright/

# headed
pytest examples/playwright/ --headed
```

## How it works

Your existing Playwright code stays as-is. Add Qirabot where you need AI:

```python
import pytest
from qirabot import Qirabot

@pytest.fixture(scope="session")
def bot():
    # One shared Qirabot task for the run; closed after the last test.
    with Qirabot(task_name="my-test") as bot:
        yield bot

def test_login(page, bot):
    # Your existing Playwright code
    page.goto("https://myapp.com")
    page.fill("#user", "admin")
    page.fill("#password", "secret")
    page.click("#login")

    # Bolt-on: AI verifies the result visually
    assert bot.verify(page, "Dashboard is displayed")

    # Bolt-on: AI extracts data from the page
    title = bot.extract(page, "What is the welcome message?")
    assert "Welcome" in title
```

`page` comes from `pytest-playwright` — no setup code needed.

## When to use AI vs selectors

| Use selectors | Use Qirabot AI |
|---|---|
| Known, stable CSS/ID selectors | Fuzzy descriptions ("the Login button") |
| Form filling (`page.fill`) | Visual assertions ("error message is red") |
| Navigation (`page.goto`) | Complex widgets (date pickers, dropdowns) |
| Counting elements | Extracting unstructured text |

## Examples

- [test_ecommerce.py](test_ecommerce.py) — Login, add to cart, checkout
- [test_todo_app.py](test_todo_app.py) — Add, complete, delete todos
- [test_form_validation.py](test_form_validation.py) — Form errors, date picker
