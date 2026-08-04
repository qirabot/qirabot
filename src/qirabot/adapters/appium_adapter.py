"""Appium WebDriver adapter for Android and iOS."""

from __future__ import annotations

import base64
import io
from typing import Any

from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig


class AppiumAdapter(DeviceAdapter):
    """Adapter for appium.webdriver.webdriver.WebDriver (Android + iOS)."""

    def __init__(self, driver: Any) -> None:
        self._driver = driver
        caps = driver.capabilities or {}
        self._platform = (caps.get("platformName") or "").lower()
        self._annotation_scale: float | None = None

    @classmethod
    def accepts(cls, target: Any) -> bool:
        t = type(target)
        return t.__module__.startswith("appium.")

    @property
    def current_target(self) -> Any:
        return self._driver

    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        cfg = config or ScreenshotConfig()
        png_bytes = base64.b64decode(self._driver.get_screenshot_as_base64())
        # iOS screenshots come back at physical pixels (Retina 2x/3x) but
        # get_window_size() reports logical points; cache the ratio so report
        # annotations can be drawn at the visual click position. Probe once,
        # cheaply, by decoding only the PNG header here when scale isn't known
        # yet (PIL lazy-loads; the full decode below for JPEG re-uses the same
        # bytes). Best-effort: any failure leaves scale unset (defaults to 1.0).
        if self._platform == "ios" and self._annotation_scale is None:
            try:
                from PIL import Image

                with Image.open(io.BytesIO(png_bytes)) as probe:
                    size = self._driver.get_window_size()
                    logical_w = size.get("width") or 0
                    if logical_w:
                        self._annotation_scale = probe.width / logical_w
            except Exception:
                pass
        return self._reencode_png(png_bytes, cfg)

    def annotation_scale(self) -> float:
        # Set lazily by screenshot() on iOS; defaults to 1.0 everywhere else
        # (Android: window_size and screenshot already share pixel space).
        return self._annotation_scale if self._annotation_scale else 1.0

    def _tap(self, x: float, y: float, pause: float = 0.1) -> None:
        from selenium.webdriver.common.action_chains import ActionChains

        # add_pointer_input takes (kind, name) strings and builds the
        # PointerInput itself — passing a PointerInput object raises TypeError,
        # which ai()'s loop swallows so taps silently no-op. The pause between
        # down/up makes the tap register reliably on real devices; a longer
        # pause turns the same gesture into a long press.
        actions = ActionChains(self._driver)
        touch = actions.w3c_actions.add_pointer_input("touch", "finger")
        touch.create_pointer_move(x=int(x), y=int(y), duration=0)
        touch.create_pointer_down(button=0)
        touch.create_pause(pause)
        touch.create_pointer_up(button=0)
        actions.perform()

    def click(self, x: float, y: float) -> None:
        self._tap(x, y)

    def double_click(self, x: float, y: float) -> None:
        self._tap(x, y)
        self._tap(x, y)

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        # Same pointer sequence as a tap, just holding for `duration` seconds.
        self._tap(x, y, pause=duration)

    def _focused_element(self) -> Any:
        """Return the currently focused input element.

        The WebDriver object itself has no send_keys (that lives on elements),
        so typing must go through the focused element. On Android the
        focused(true) UiSelector is the most reliable — it finds the active
        field even when it's an AutoCompleteTextView / custom widget rather than
        a plain EditText. active_element is the cross-platform fallback (iOS).
        """
        if self._platform == "android":
            from appium.webdriver.common.appiumby import AppiumBy

            try:
                return self._driver.find_element(
                    AppiumBy.ANDROID_UIAUTOMATOR, "new UiSelector().focused(true)"
                )
            except Exception:
                pass
        return self._driver.switch_to.active_element

    def type_text(self, x: float, y: float, text: str) -> None:
        self._tap(x, y)
        self.type_focused(text)

    def type_focused(self, text: str) -> None:
        self._focused_element().send_keys(text)

    def clear_text(self, x: float, y: float) -> None:
        self._tap(x, y)
        self.clear_focused()

    def clear_focused(self) -> None:
        el = self._focused_element()
        if el:
            el.clear()

    def press_key(self, key: str) -> None:
        if self._platform == "android":
            key_map = {
                "enter": 66, "back": 4, "home": 3, "menu": 82,
                "volume_up": 24, "volume_down": 25, "power": 26,
                "tab": 61, "delete": 67, "backspace": 67,
            }
            code = key_map.get(key.lower())
            if code is not None:
                self._driver.press_keycode(code)
            else:
                self._focused_element().send_keys(key)
        else:
            # iOS：键名要转成 XCUITest 能识别的字符，否则会被当文本输入。
            # 回车需发 "\n" 触发键盘 return/搜索键（直接发 "Enter" 会输出字面文字）。
            ios_key_map = {"enter": "\n", "return": "\n", "tab": "\t"}
            self._focused_element().send_keys(ios_key_map.get(key.lower(), key))

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        from selenium.webdriver.common.action_chains import ActionChains

        # See _tap: add_pointer_input wants (kind, name) strings, not a
        # PointerInput object.
        actions = ActionChains(self._driver)
        touch = actions.w3c_actions.add_pointer_input("touch", "finger")
        touch.create_pointer_move(x=int(from_x), y=int(from_y), duration=0)
        touch.create_pointer_down(button=0)
        touch.create_pause(0.5)
        touch.create_pointer_move(x=int(to_x), y=int(to_y), duration=500)
        touch.create_pointer_up(button=0)
        actions.perform()

    # Mobile transitions/animations/app launches; see
    # ``DeviceAdapter.settle_seconds`` for the rationale and override mechanism.
    _SETTLE_SECONDS = 0.6

    # ---- scrolling (shared geometry: DeviceAdapter._swipe_scroll) ------------

    def scroll(self, x: float, y: float, direction: str, distance: int) -> None:
        # DeviceAdapter contract / direct callers keep the legacy ×100 unit.
        self._scroll_pixels(x, y, direction, distance * 100)

    def _scroll_pixels(self, x: float, y: float, direction: str, pixels: int) -> None:
        info = self.device_info()
        self._swipe_scroll(
            x or info.width / 2.0, y or info.height / 2.0, direction, pixels, info
        )

    def _swipe_from_to(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        self.drag(from_x, from_y, to_x, to_y)

    def navigate(self, url: str) -> None:
        self._driver.get(url)

    def go_back(self) -> None:
        self._driver.back()

    def device_info(self) -> DeviceInfo:
        size = self._driver.get_window_size()
        platform = self._platform or "android"
        return DeviceInfo(
            platform=platform,
            width=size.get("width", 1080),
            height=size.get("height", 1920),
        )
