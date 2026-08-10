"""Tests for the optional-dependency install hints (qirabot._optional.require)."""

from unittest.mock import MagicMock

import pytest

from qirabot import _optional
from qirabot.exceptions import MissingDependencyError


@pytest.fixture
def missing_everything(monkeypatch):
    """Make every import fail so the hints are testable regardless of what the
    dev environment actually has installed."""
    monkeypatch.setattr(
        _optional.importlib, "import_module", MagicMock(side_effect=ImportError)
    )


def test_extra_package_hints_the_qirabot_extra(missing_everything):
    with pytest.raises(MissingDependencyError) as ei:
        _optional.require("playwright.sync_api")
    assert 'uv pip install "qirabot[browser]"' in str(ei.value)


def test_selenium_hints_plain_package_install(missing_everything):
    """Selenium is deliberately not an extra (bring-your-own driver); the hint
    must not point at a nonexistent qirabot[selenium]."""
    with pytest.raises(MissingDependencyError) as ei:
        _optional.require("selenium.webdriver")
    msg = str(ei.value)
    assert "uv pip install selenium" in msg
    assert "qirabot[" not in msg


def test_explicit_extra_still_wins(missing_everything):
    # An explicit extra overrides the top-level-package inference ("appium"
    # would infer "appium" anyway; use a mismatched pair to prove precedence).
    with pytest.raises(MissingDependencyError) as ei:
        _optional.require("some_backend.api", "appium")
    assert 'uv pip install "qirabot[appium]"' in str(ei.value)


@pytest.fixture
def uv_tool_env(monkeypatch):
    """Pretend qirabot was installed with `uv tool install` (the one-line
    installer's default), where a pip hint would target the wrong Python."""
    monkeypatch.setattr(_optional, "_uv_tool_env", lambda: True)
    monkeypatch.setattr(_optional, "_version_pin", lambda: "==2.0.0")


def test_uv_env_hints_uv_tool_install(missing_everything, uv_tool_env, monkeypatch):
    monkeypatch.setattr(_optional, "_installed_extras", lambda: set())
    with pytest.raises(MissingDependencyError) as ei:
        _optional.require("pyautogui")
    msg = str(ei.value)
    assert 'uv tool install --force "qirabot[desktop]==2.0.0"' in msg
    assert "pip" not in msg


def test_uv_env_hint_keeps_installed_extras(missing_everything, uv_tool_env, monkeypatch):
    # `uv tool install` replaces the environment wholesale — the hint must
    # carry the extras already present or it would uninstall them.
    monkeypatch.setattr(_optional, "_installed_extras", lambda: {"browser"})
    with pytest.raises(MissingDependencyError) as ei:
        _optional.require("pyautogui")
    assert 'uv tool install --force "qirabot[browser,desktop]==2.0.0"' in str(ei.value)


def test_uv_env_selenium_hint_uses_with(missing_everything, uv_tool_env, monkeypatch):
    monkeypatch.setattr(_optional, "_installed_extras", lambda: {"browser"})
    with pytest.raises(MissingDependencyError) as ei:
        _optional.require("selenium.webdriver")
    assert (
        'uv tool install --force --with selenium "qirabot[browser]==2.0.0"'
        in str(ei.value)
    )


def test_uv_env_selenium_hint_without_extras(missing_everything, uv_tool_env, monkeypatch):
    monkeypatch.setattr(_optional, "_installed_extras", lambda: set())
    with pytest.raises(MissingDependencyError) as ei:
        _optional.require("selenium.webdriver")
    assert 'uv tool install --force --with selenium "qirabot==2.0.0"' in str(ei.value)
