"""Auto-detect adapter for a given target object."""

from __future__ import annotations

import logging
from typing import Any

from qirabot.adapters.adb_adapter import AdbAdapter
from qirabot.adapters.appium_adapter import AppiumAdapter
from qirabot.adapters.base import DeviceAdapter
from qirabot.adapters.playwright_adapter import PlaywrightAdapter
from qirabot.adapters.pyautogui_adapter import PyAutoGuiAdapter
from qirabot.adapters.selenium_adapter import SeleniumAdapter
from qirabot.adapters.wda_adapter import WdaAdapter
from qirabot.adapters.windows_adapter import WindowsAdapter

logger = logging.getLogger("qirabot")

_ADAPTER_CLASSES: list[type[DeviceAdapter]] = [
    PlaywrightAdapter,
    AppiumAdapter,
    SeleniumAdapter,
    AdbAdapter,
    WdaAdapter,
    WindowsAdapter,
    PyAutoGuiAdapter,
]


def register_adapter(cls: type[DeviceAdapter]) -> None:
    """Register a third-party :class:`DeviceAdapter` for target auto-detection.

    After registration, ``bind()`` and every action method accept whatever
    targets ``cls.accepts()`` recognizes — this is how backends qirabot does
    not ship (airtest, cloud-device SDKs, custom engine bridges) plug in::

        from qirabot import register_adapter
        register_adapter(AirtestAdapter)   # once, at import/startup time
        bot = Qirabot().bind(connect_device("Android:///emu-5554"))

    Custom adapters are checked BEFORE the built-ins so a broad built-in
    ``accepts()`` cannot shadow them. Registering the same class twice is a
    no-op. Alternatively, skip registration entirely and pass an adapter
    instance straight to ``bind()`` — :func:`detect` uses it as-is.
    """
    if not (isinstance(cls, type) and issubclass(cls, DeviceAdapter)):
        raise TypeError(
            f"register_adapter() expects a DeviceAdapter subclass, got {cls!r}"
        )
    if cls not in _ADAPTER_CLASSES:
        _ADAPTER_CLASSES.insert(0, cls)


def _is_airtest_target(target: Any) -> bool:
    """Tombstone check: recognize the airtest targets 1.x accepted, by module
    name strings only (zero imports — airtest is no longer a dependency)."""
    if getattr(target, "__name__", "") == "airtest.core.api":
        return True
    if getattr(target, "__name__", "") == "G" and str(
        getattr(target, "__module__", "")
    ).startswith("airtest."):
        return True
    return type(target).__module__.startswith("airtest.")


def detect(target: Any) -> DeviceAdapter:
    """Detect the appropriate adapter for a target object.

    A ready-made :class:`DeviceAdapter` instance is used as-is, so custom
    backends work without touching the registry:
    ``Qirabot().bind(MyAdapter(...))``.
    """
    if isinstance(target, DeviceAdapter):
        return target
    for cls in _ADAPTER_CLASSES:
        if cls.accepts(target):
            return cls(target)
    if _is_airtest_target(target):
        raise TypeError(
            "airtest targets are not supported directly. Use "
            "Android -> qirabot.AdbDevice (direct adb, zero dependencies), "
            "Windows -> qirabot.Window(hwnd=/title=/title_re=), "
            "iOS -> qirabot.WdaClient(wda_url). To keep driving airtest, copy "
            "examples/airtest/adapter.py into your project and "
            "register_adapter(AirtestAdapter)."
        )
    raise TypeError(
        f"Unsupported target type: {type(target).__name__}. "
        f"Supported: playwright Page, appium WebDriver, selenium WebDriver, "
        f"qirabot.AdbDevice, qirabot.WdaClient, qirabot.Window, pyautogui module"
    )
