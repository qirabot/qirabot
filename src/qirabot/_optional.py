"""Lazy loading of optional backend dependencies.

Each device backend (browser/desktop/appium) ships as an ``[project.optional-
dependencies]`` extra. Rather than importing those modules eagerly — or letting a
missing one surface as a raw ``ModuleNotFoundError`` traceback — call :func:`require`
at the point of use. It returns the imported module on success, or raises a
:class:`~qirabot.exceptions.MissingDependencyError` whose message tells the user the
exact install command to run — a ``uv tool install`` when qirabot itself lives in a
uv tool environment (the one-line installer's default), otherwise
``uv pip install "qirabot[<extra>]"``.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import warnings
from pathlib import Path
from types import ModuleType

from qirabot.exceptions import MissingDependencyError

# Top-level package -> the extra that provides it, so we can infer the install hint
# from a dotted import path (e.g. "playwright.sync_api" -> "browser") without the
# caller having to repeat it.
_EXTRA_FOR_PACKAGE = {
    "playwright": "browser",
    "pyautogui": "desktop",
    "pyperclip": "desktop",
    "appium": "appium",
}

# Packages that are deliberately NOT qirabot extras (bring-your-own-driver): the
# hint must install the bare package, not a nonexistent qirabot[<extra>].
_NOT_AN_EXTRA = frozenset({"selenium"})


def _uv_tool_env() -> bool:
    """True when qirabot runs from a ``uv tool install`` environment.

    That's what the one-line installers set up. In it, ``uv pip install``
    targets the active venv — never this environment — so the library-install
    hint would send the user in a circle. uv marks each tool environment with a
    ``uv-receipt.toml`` at its root; plain venv setups have none.
    """
    return (Path(sys.prefix) / "uv-receipt.toml").is_file()


def _installed_extras() -> set[str]:
    """The qirabot extras whose packages are importable right now.

    ``uv tool install`` replaces the environment with exactly the requested
    requirements, so a hint that names only the missing extra would silently
    uninstall the ones the user already has (the installer's ``[browser]``,
    typically). Fold the present ones back in.
    """
    extras = set()
    for package, extra in _EXTRA_FOR_PACKAGE.items():
        try:
            if importlib.util.find_spec(package) is not None:
                extras.add(extra)
        except (ImportError, ValueError):
            pass
    return extras


def _version_pin() -> str:
    """``==<running version>``, or empty when the version can't be determined.

    Without the pin, a fresh ``uv tool install`` re-resolves from PyPI and won't
    pick a pre-release — a user on 2.0.0rc2 asking for the desktop extra would be
    silently downgraded to the latest stable.
    """
    try:
        from importlib.metadata import version

        return f"=={version('qirabot')}"
    except Exception:
        return ""


def extra_install_hint(extra: str) -> str:
    """Shell command that adds qirabot's ``<extra>`` to *this* environment."""
    if _uv_tool_env():
        extras = ",".join(sorted(_installed_extras() | {extra}))
        return f'uv tool install --force "qirabot[{extras}]{_version_pin()}"'
    return f'uv pip install "qirabot[{extra}]"'


def package_install_hint(package: str) -> str:
    """Shell command that adds a bare (non-extra) package to *this* environment."""
    if _uv_tool_env():
        extras = ",".join(sorted(_installed_extras()))
        base = f"qirabot[{extras}]" if extras else "qirabot"
        return f'uv tool install --force --with {package} "{base}{_version_pin()}"'
    return f"uv pip install {package}"


def require(module: str, extra: str | None = None) -> ModuleType:
    """Import an optional dependency or raise an actionable install hint.

    Args:
        module: import path, dotted paths allowed (e.g. ``"playwright.sync_api"``).
        extra: the ``qirabot[<extra>]`` that provides it. Inferred from the
            top-level package when omitted.

    Returns:
        The imported module.

    Raises:
        MissingDependencyError: if the module is not installed.
    """
    try:
        # Some third-party backends have shipped pre-3.12 escape sequences
        # ('\d' in docstrings etc.); on Python >=3.12 their first bytecode
        # compile emits SyntaxWarnings that read like qirabot errors. Not our
        # code to fix, so silence them for the import only.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            return importlib.import_module(module)
    except ImportError as e:
        package = module.split(".")[0]
        if package in _NOT_AN_EXTRA:
            hint = package_install_hint(package)
        else:
            which = extra or _EXTRA_FOR_PACKAGE.get(package, package)
            hint = extra_install_hint(which)
        raise MissingDependencyError(
            f"'{package}' is required for this feature but is not installed. "
            f"Install it with:  {hint}"
        ) from e
