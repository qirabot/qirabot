"""Bolt-on AI to Playwright: test a Todo app.

Install:
    uv pip install qirabot pytest-playwright
    playwright install chromium

Run:
    pytest examples/playwright/test_todo_app.py
"""

import pytest

from qirabot import Qirabot

URL = "https://todomvc.com/examples/react/dist/"


@pytest.fixture(scope="session")
def bot():
    # Session-scoped: all tests share one Qirabot task, and the with-block
    # closes it (reporting the run's status) after the last test. A fixture —
    # not module level — so collection alone starts nothing.
    with Qirabot(task_name="test-todo") as bot:
        yield bot


def test_add_todo(page, bot):
    page.goto(URL)

    # Your existing code
    page.fill(".new-todo", "Buy groceries")
    page.press(".new-todo", "Enter")

    # Bolt-on: AI checks if the todo appeared
    assert bot.verify(page, "'Buy groceries' is in the todo list")


def test_complete_todo(page, bot):
    page.goto(URL)
    page.fill(".new-todo", "Buy groceries")
    page.press(".new-todo", "Enter")

    # Your existing code
    page.click(".todo-list li .toggle")

    # Bolt-on: AI checks strikethrough — hard to assert with selectors
    assert bot.verify(page, "'Buy groceries' has strikethrough style")


def test_delete_todo(page, bot):
    page.goto(URL)
    page.fill(".new-todo", "Temp task")
    page.press(".new-todo", "Enter")

    # Bolt-on: delete button only shows on hover, AI handles it
    result = bot.ai(page, "Hover 'Temp task' and click the delete button", max_steps=5)
    assert result.success
    assert not bot.verify(page, "'Temp task' is visible")


def test_count_todos(page, bot):
    page.goto(URL)
    for task in ["Task A", "Task B", "Task C"]:
        page.fill(".new-todo", task)
        page.press(".new-todo", "Enter")

    # Bolt-on: AI extracts count
    count = bot.extract(page, "How many todos are in the list?")
    assert "3" in count
