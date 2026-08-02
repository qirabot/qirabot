# Selenium + Qirabot

Use Qirabot as a bolt-on AI layer on top of your existing Selenium tests.

## Install

```bash
python -m pip install qirabot selenium pytest
gcloud auth application-default login   # auth: Google Cloud ADC, once
```

Make sure ChromeDriver is installed and in your PATH.

## Run

```bash
pytest examples/selenium/
```

## How it works

Your existing Selenium code stays as-is. Add Qirabot where you need AI:

```python
import pytest
from selenium import webdriver
from selenium.webdriver.common.by import By
from qirabot import Qirabot

@pytest.fixture(scope="session")
def driver():
    driver = webdriver.Chrome()
    yield driver
    driver.quit()

@pytest.fixture(scope="session")
def bot(driver):
    # bind once; the driver is stable across the session. Closed after the last test.
    with Qirabot(task_name="my-test").bind(driver) as bot:
        yield bot

def test_search(driver, bot):
    driver.get("https://myapp.com")

    # Your existing Selenium code
    driver.find_element(By.ID, "search").send_keys("hello")
    driver.find_element(By.ID, "submit").click()

    # Bolt-on: AI checks the result
    assert bot.verify("Search results are displayed")

    # Bolt-on: AI extracts data
    first = bot.extract("What is the first search result?")
    assert first
```

## Examples

- [test_ecommerce.py](test_ecommerce.py) — Login, product listing, checkout
- [test_search_engine.py](test_search_engine.py) — Wikipedia search, article extraction
