"""Bolt-on AI to Selenium: test an e-commerce site.

Site: https://www.saucedemo.com

Run:
    pytest examples/selenium/test_ecommerce.py
"""

import pytest
from selenium import webdriver
from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from qirabot import Qirabot


@pytest.fixture(scope="session")
def driver():
    driver = webdriver.Chrome()
    yield driver
    driver.quit()


@pytest.fixture(scope="session")
def bot(driver):
    # bind(driver) once so AI calls drop the repeated first argument; the
    # with-block closes the Qirabot task after the last test.
    with Qirabot(task_name="test-ecommerce-selenium").bind(driver) as bot:
        yield bot


def login(driver):
    """Log in and wait for the inventory page to finish loading.

    Retries once: the login submit occasionally doesn't navigate in time,
    leaving us on the login page with the form still present.
    """
    for attempt in range(2):
        driver.get("https://www.saucedemo.com")
        driver.find_element(By.ID, "user-name").send_keys("standard_user")
        driver.find_element(By.ID, "password").send_keys("secret_sauce")
        driver.find_element(By.ID, "login-button").click()
        try:
            WebDriverWait(driver, 10).until(EC.url_contains("inventory.html"))
            return
        except TimeoutException:
            if attempt == 1:
                raise


def test_login(driver, bot):
    login(driver)

    # Bolt-on: AI visual check
    assert bot.verify("Product listing is displayed")


def test_extract_products(driver, bot):
    login(driver)

    # Your existing code
    items = driver.find_elements(By.CLASS_NAME, "inventory_item")
    assert len(items) == 6

    # Bolt-on: AI extracts product info
    info = bot.extract("List the first 3 product names and prices")
    assert "Sauce Labs" in info


def test_checkout(driver, bot):
    login(driver)

    # Your existing code
    driver.find_element(By.CSS_SELECTOR, '[data-test="add-to-cart-sauce-labs-backpack"]').click()
    driver.find_element(By.CLASS_NAME, "shopping_cart_link").click()

    # Bolt-on: AI finishes checkout
    result = bot.ai(
        "Click checkout, fill name 'Jane Doe', zip '90210', complete the order",
        max_steps=10,
    )
    assert result.success
