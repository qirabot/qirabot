"""Selenium WebDriver adapter."""

from __future__ import annotations

import base64
from typing import Any

from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig, split_combo


class SeleniumAdapter(DeviceAdapter):
    """Adapter for selenium.webdriver.remote.webdriver.WebDriver."""

    def __init__(self, driver: Any) -> None:
        self._driver = driver

    @classmethod
    def accepts(cls, target: Any) -> bool:
        t = type(target)
        return t.__module__.startswith("selenium.") and "WebDriver" in t.__name__

    @property
    def current_target(self) -> Any:
        # The driver object is stable across window/tab switches (focus moves via
        # switch_to, the object stays the same), so it is always the target.
        return self._driver

    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        cfg = config or ScreenshotConfig()
        png_bytes = base64.b64decode(self._driver.get_screenshot_as_base64())
        return self._reencode_png(png_bytes, cfg)

    def _pointer(self) -> Any:
        """A fresh W3C ActionBuilder whose pointer uses viewport-origin coords.

        The (x, y) we receive are viewport pixels with a top-left origin (what
        the model reads off a screenshot). ``move_to_location`` moves the pointer
        to those absolute coordinates; ``move_to_element_with_offset(body, ...)``
        must NOT be used here -- in Selenium 4 its offset is measured from the
        element's center, so screenshot coordinates land in the wrong place (or
        raise MoveTargetOutOfBounds).
        """
        from selenium.webdriver.common.actions.action_builder import ActionBuilder
        return ActionBuilder(self._driver)

    def click(self, x: float, y: float) -> None:
        ab = self._pointer()
        ab.pointer_action.move_to_location(int(x), int(y)).click()
        ab.perform()

    def double_click(self, x: float, y: float) -> None:
        ab = self._pointer()
        ab.pointer_action.move_to_location(int(x), int(y)).double_click()
        ab.perform()

    def right_click(self, x: float, y: float) -> None:
        ab = self._pointer()
        ab.pointer_action.move_to_location(int(x), int(y)).context_click()
        ab.perform()

    def hover(self, x: float, y: float) -> None:
        ab = self._pointer()
        ab.pointer_action.move_to_location(int(x), int(y))
        ab.perform()

    def type_text(self, x: float, y: float, text: str) -> None:
        self.click(x, y)
        self.type_focused(text)

    def type_focused(self, text: str) -> None:
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(self._driver).send_keys(text).perform()

    def clear_text(self, x: float, y: float) -> None:
        self.click(x, y)
        self.clear_focused()

    def clear_focused(self) -> None:
        from selenium.webdriver.common.keys import Keys
        from selenium.webdriver.common.action_chains import ActionChains
        ActionChains(self._driver).key_down(Keys.CONTROL).send_keys("a").key_up(Keys.CONTROL).send_keys(Keys.BACKSPACE).perform()

    def press_key(self, key: str) -> None:
        from selenium.webdriver.common.action_chains import ActionChains
        from selenium.webdriver.common.keys import Keys

        # send_keys() treats its argument as literal characters, so "ctrl+c" or
        # "Enter" would be typed verbatim. Map modifiers to held keys and the
        # final key to its Keys.* code (plain characters pass through unchanged).
        modifiers = {
            "ctrl": Keys.CONTROL, "control": Keys.CONTROL, "alt": Keys.ALT,
            "option": Keys.ALT, "shift": Keys.SHIFT, "cmd": Keys.META,
            "command": Keys.META, "meta": Keys.META, "win": Keys.META,
        }
        special = {
            "enter": Keys.ENTER, "return": Keys.ENTER, "tab": Keys.TAB,
            "escape": Keys.ESCAPE, "esc": Keys.ESCAPE, "backspace": Keys.BACK_SPACE,
            "delete": Keys.DELETE, "del": Keys.DELETE, "space": Keys.SPACE,
            "arrowup": Keys.ARROW_UP, "arrowdown": Keys.ARROW_DOWN,
            "arrowleft": Keys.ARROW_LEFT, "arrowright": Keys.ARROW_RIGHT,
            "pageup": Keys.PAGE_UP, "pagedown": Keys.PAGE_DOWN,
            "home": Keys.HOME, "end": Keys.END,
        }
        # NOTE: browser-level shortcuts like ctrl+w / ctrl+t generally do not work
        # through WebDriver's synthetic key events; only page-level combos do.
        mods, base = split_combo(key)
        held = [modifiers[m.lower()] for m in mods if m.lower() in modifiers]
        base_key = special.get(base.lower(), base)
        actions = ActionChains(self._driver)
        for m in held:
            actions.key_down(m)
        actions.send_keys(base_key)
        for m in reversed(held):
            actions.key_up(m)
        actions.perform()

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        ab = self._pointer()
        ab.pointer_action.move_to_location(int(from_x), int(from_y)).click_and_hold().move_to_location(
            int(to_x), int(to_y)
        ).release()
        ab.perform()

    def navigate(self, url: str) -> None:
        self._driver.get(url)

    def go_back(self) -> None:
        self._driver.back()

    def scroll(self, x: float, y: float, direction: str, distance: int) -> None:
        delta = distance * 100
        if direction == "down":
            self._driver.execute_script(f"window.scrollBy(0, {delta})")
        elif direction == "up":
            self._driver.execute_script(f"window.scrollBy(0, {-delta})")
        elif direction == "right":
            self._driver.execute_script(f"window.scrollBy({delta}, 0)")
        elif direction == "left":
            self._driver.execute_script(f"window.scrollBy({-delta}, 0)")

    # Page navigation/AJAX/DOM updates/smooth-scroll; Selenium's coordinate-level
    # actions don't wait for the effects they trigger (implicit waits only cover
    # find_element). See ``DeviceAdapter.settle_seconds`` for the override mechanism.
    _SETTLE_SECONDS = 0.6

    def device_info(self) -> DeviceInfo:
        # Report the viewport (what the screenshot captures and what click
        # coordinates are measured against), NOT the outer window size from
        # get_window_size() -- the latter includes browser chrome, so it
        # disagrees with the screenshot height and skews the model's coords.
        try:
            w, h = self._driver.execute_script(
                "return [window.innerWidth, window.innerHeight];"
            )
        except Exception:
            size = self._driver.get_window_size()
            w, h = size.get("width", 1280), size.get("height", 720)
        return DeviceInfo(platform="browser", width=int(w), height=int(h))
