"""Bolt-on AI to Playwright: test an e-commerce site.

Install:
    uv pip install qirabot pytest-playwright
    playwright install chromium

Run:
    pytest examples/playwright/test_ecommerce.py
    pytest examples/playwright/test_ecommerce.py --headed
"""

import pytest

from qirabot import Qirabot


@pytest.fixture(scope="session")
def bot():
    # One shared Qirabot task for the whole run; closed (status reported)
    # after the last test.
    with Qirabot(task_name="test-ecommerce") as bot:
        yield bot


def test_login(page, bot):
    page.goto("https://www.saucedemo.com")

    # Your existing Playwright code
    page.fill("#user-name", "standard_user")
    page.fill("#password", "secret_sauce")
    page.click("#login-button")

    # Bolt-on: AI verifies the result
    assert bot.verify(page, "Product listing page is displayed")


def test_login_error(page, bot):
    page.goto("https://www.saucedemo.com")
    page.fill("#user-name", "bad_user")
    page.fill("#password", "bad_pass")
    page.click("#login-button")

    # Bolt-on: check error message without knowing exact text
    assert bot.verify(page, "An error message is shown")


def test_add_to_cart(page, bot):
    page.goto("https://www.saucedemo.com")
    page.fill("#user-name", "standard_user")
    page.fill("#password", "secret_sauce")
    page.click("#login-button")

    # Your existing code
    page.click('[data-test="add-to-cart-sauce-labs-backpack"]')

    # Bolt-on: AI checks the cart badge
    assert bot.verify(page, "Shopping cart badge shows 1")

    # Bolt-on: AI extracts product info
    name = bot.extract(page, "What is the name of the first product?")
    assert "Backpack" in name


def test_checkout(page, bot):
    page.goto("https://www.saucedemo.com")
    page.fill("#user-name", "standard_user")
    page.fill("#password", "secret_sauce")
    page.click("#login-button")
    page.click('[data-test="add-to-cart-sauce-labs-backpack"]')
    page.click(".shopping_cart_link")

    # Bolt-on: AI handles the checkout form
    result = bot.ai(
        page,
        "Click checkout, fill name 'John Doe', zip '10001', finish the order",
        max_steps=10,
    )
    assert result.success
