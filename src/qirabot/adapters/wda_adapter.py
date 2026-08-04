"""Direct-WDA iOS adapter (no Appium server, no facebook-wda).

Drives a :class:`~qirabot.wda.WdaClient`. Coordinates follow the Appium-iOS
convention already used by :class:`AppiumAdapter`: ``device_info`` reports
logical points (WDA's own coordinate space), screenshots come back at physical
pixels, and ``annotation_scale`` carries the Retina ratio so report crosshairs
land where the tap happened.
"""

from __future__ import annotations

import io
import time
from typing import Any

from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig, split_combo
from qirabot.wda import WdaClient

# press_key names → WDA pressButton names (the whole set WDA accepts). home is
# handled separately via the sessionless /wda/homescreen route.
_BUTTONS = {
    "volumeup": "volumeUp",
    "volume_up": "volumeUp",
    "volumedown": "volumeDown",
    "volume_down": "volumeDown",
}


class WdaAdapter(DeviceAdapter):
    """Adapter for :class:`qirabot.wda.WdaClient` targets."""

    # WDA animates gestures and returns promptly; iOS transitions are quick, so
    # a smaller floor than Android's (see DeviceAdapter.settle_seconds).
    _SETTLE_SECONDS = 0.6

    # Delay between the focusing tap and the first keystroke, so the keyboard
    # finishes appearing before characters arrive.
    _FOCUS_SETTLE = 0.3

    def __init__(self, target: Any) -> None:
        self._client: WdaClient = target
        self._size: tuple[int, int] | None = None  # logical points, cached
        self._annotation_scale: float | None = None

    @classmethod
    def accepts(cls, target: Any) -> bool:
        return isinstance(target, WdaClient)

    @property
    def current_target(self) -> Any:
        return self._client

    # ---- screen -------------------------------------------------------------

    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        cfg = config or ScreenshotConfig()
        png = self._client.screenshot()
        # Screenshots are physical pixels, window_size is logical points; probe
        # the ratio once (PNG header only) so report annotations can be drawn
        # at the visual tap position. Best-effort, like the Appium adapter.
        if self._annotation_scale is None:
            try:
                from PIL import Image

                with Image.open(io.BytesIO(png)) as probe:
                    logical_w = self._window_size()[0]
                    if logical_w:
                        self._annotation_scale = probe.width / logical_w
            except Exception:
                pass
        return self._reencode_png(png, cfg)

    def annotation_scale(self) -> float:
        return self._annotation_scale if self._annotation_scale else 1.0

    def _window_size(self) -> tuple[int, int]:
        if self._size is None:
            self._size = self._client.window_size()
        return self._size

    def device_info(self) -> DeviceInfo:
        width, height = self._window_size()
        return DeviceInfo(platform="ios", width=width, height=height)

    # ---- pointer ------------------------------------------------------------

    def click(self, x: float, y: float) -> None:
        self._client.tap(int(x), int(y))

    def double_click(self, x: float, y: float) -> None:
        self._client.double_tap(int(x), int(y))

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        self._client.tap_hold(int(x), int(y), duration)

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        self._client.swipe(
            int(from_x), int(from_y), int(to_x), int(to_y), duration=0.5
        )

    # ---- scrolling (shared geometry: DeviceAdapter._swipe_scroll) ------------

    def scroll(self, x: float, y: float, direction: str, distance: int) -> None:
        self._scroll_pixels(x, y, direction, distance * 100)

    def _scroll_pixels(self, x: float, y: float, direction: str, pixels: int) -> None:
        info = self.device_info()
        self._swipe_scroll(
            x or info.width / 2.0, y or info.height / 2.0, direction, pixels, info
        )

    def _swipe_from_to(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        # Instant flick (duration=0), unlike drag()'s deliberate 0.5s hold.
        self._client.swipe(int(from_x), int(from_y), int(to_x), int(to_y), duration=0)

    # ---- keys / text ---------------------------------------------------------

    def press_key(self, key: str) -> None:
        _mods, base = split_combo(key)  # iOS has no held-modifier concept
        k = base.lower()
        if k in ("enter", "return"):
            self._client.send_keys("\n")
        elif k in ("backspace", "delete", "del"):
            self._client.send_keys("\b")
        elif k == "home":
            self._client.home()
        elif k in _BUTTONS:
            self._client.press_button(_BUTTONS[k])
        elif k in ("lock", "power"):
            self._client.lock()
        else:
            raise NotImplementedError(f"iOS does not support key {key!r}")

    def go_back(self) -> None:
        # iOS has no back button; the universal gesture is a left-edge swipe.
        w, h = self._window_size()
        self._client.swipe(1, int(h * 0.5), int(w * 0.6), int(h * 0.5), duration=0)

    def type_text(self, x: float, y: float, text: str) -> None:
        self.click(x, y)
        time.sleep(self._FOCUS_SETTLE)
        self.type_focused(text)

    def type_focused(self, text: str) -> None:
        if text:
            self._client.send_keys(text)

    def clear_focused(self) -> None:
        # No element model here: a burst of backspaces is the WDA-level best
        # effort (one request — /wda/keys takes the whole list at once).
        self._client.send_keys("\b" * 64)
