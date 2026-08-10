"""Airtest adapter for qirabot — copy this file into YOUR project.

qirabot has no airtest integration (and none of its numpy<2 /
opencv-contrib pins). This reference adapter adds one on the user side:
airtest stays a dependency of *your* project, not of qirabot.

    Reference implementation, not shipped inside the qirabot package.
    Tested with airtest 1.3.x. If an airtest API drifts, fix it here —
    the file is yours once copied.

Usage A — register once, then airtest-style code runs unchanged:

    from airtest.core.api import connect_device
    from qirabot import Qirabot, register_adapter
    from .adapter import AirtestAdapter

    register_adapter(AirtestAdapter)
    bot = Qirabot().bind(connect_device("Android:///emulator-5554"))
    bot.ai("Open the inventory and list all items")

Usage B — explicit passthrough, no registration:

    bot = Qirabot().bind(AirtestAdapter(connect_device(...)))
"""

from __future__ import annotations

from typing import Any

from qirabot import DeviceAdapter, DeviceInfo, ScreenshotConfig

# Android keyevent names for the key vocabulary the qirabot engine emits.
# Non-Android airtest devices (Windows/iOS) receive the raw key string.
_ANDROID_KEYEVENTS = {
    "enter": "KEYCODE_ENTER",
    "backspace": "KEYCODE_DEL",
    "delete": "KEYCODE_FORWARD_DEL",
    "tab": "KEYCODE_TAB",
    "escape": "KEYCODE_ESCAPE",
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "space": "KEYCODE_SPACE",
}


class AirtestAdapter(DeviceAdapter):
    """Drive any connected airtest device (Android/iOS/Windows) from qirabot."""

    # Touch devices repaint after airtest returns; same floor as the adb backend.
    _SETTLE_SECONDS = 0.5

    def __init__(self, device: Any = None) -> None:
        if device is None:
            from airtest.core.api import G

            device = G.DEVICE
        if device is None:
            raise ValueError(
                "no airtest device: call connect_device(...) first, "
                "or pass one explicitly: AirtestAdapter(device)"
            )
        # Accept the airtest.core.api module / G as targets; unwrap both
        # down to the active device object.
        name = getattr(device, "__name__", "")
        if name == "airtest.core.api":
            device = device.G.DEVICE
        elif name == "G":
            device = device.DEVICE
        if device is None:
            raise ValueError("airtest G.DEVICE is unset: call connect_device(...) first")
        self.device = device

    @classmethod
    def accepts(cls, target: Any) -> bool:
        # The three target shapes accepted: a device object, the
        # airtest.core.api module, and the G global.
        if type(target).__module__.startswith("airtest."):
            return True
        name = getattr(target, "__name__", "")
        return name == "airtest.core.api" or (
            name == "G" and str(getattr(target, "__module__", "")).startswith("airtest.")
        )

    # -- capture ----------------------------------------------------------

    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        import cv2  # airtest already depends on opencv

        cfg = config or ScreenshotConfig()
        frame = self.device.snapshot()  # BGR numpy array
        if cfg.format == "jpeg":
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, cfg.quality])
        else:
            ok, buf = cv2.imencode(".png", frame)
        if not ok:
            raise RuntimeError("cv2.imencode failed on airtest snapshot")
        return buf.tobytes()

    def device_info(self) -> DeviceInfo:
        if hasattr(self.device, "get_current_resolution"):
            width, height = self.device.get_current_resolution()
        else:  # e.g. Windows: fall back to the snapshot dimensions
            height, width = self.device.snapshot().shape[:2]
        platform = type(self.device).__name__.lower()  # Android / IOS / Windows
        if platform not in ("android", "ios"):
            platform = "desktop"
        return DeviceInfo(platform=platform, width=int(width), height=int(height))

    # -- pointer ----------------------------------------------------------

    def click(self, x: float, y: float) -> None:
        self.device.touch((x, y))

    def double_click(self, x: float, y: float) -> None:
        if hasattr(self.device, "double_click"):
            self.device.double_click((x, y))
        else:
            self.device.touch((x, y))
            self.device.touch((x, y))

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        self.device.touch((x, y), duration=duration)

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        self.device.swipe((from_x, from_y), (to_x, to_y))

    def scroll(self, x: float, y: float, direction: str, distance: int) -> None:
        # distance is in scroll units (~100 px each), same as the adb backend.
        # "down" scrolls content down -> finger swipes up.
        px = distance * 100
        dx, dy = {
            "down": (0, -px),
            "up": (0, px),
            "left": (px, 0),
            "right": (-px, 0),
        }.get(direction, (0, -px))
        self.device.swipe((x, y), (x + dx, y + dy))

    # -- keyboard ---------------------------------------------------------

    def type_text(self, x: float, y: float, text: str) -> None:
        self.device.touch((x, y))
        self.type_focused(text)

    def type_focused(self, text: str) -> None:
        self.device.text(text, enter=False)

    def press_key(self, key: str) -> None:
        if type(self.device).__name__ == "Android":
            key = _ANDROID_KEYEVENTS.get(key.lower(), key.upper())
        self.device.keyevent(key)

    # -- bookkeeping --------------------------------------------------------

    @property
    def current_target(self) -> Any:
        return self.device
