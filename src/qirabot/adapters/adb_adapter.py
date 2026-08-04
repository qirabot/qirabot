"""Direct-adb Android adapter (no framework, no device agent for input).

Drives an :class:`~qirabot.adb.AdbDevice` with plain ``adb shell input`` /
``screencap`` calls. Screenshot/tap/swipe/keyevent are the whole surface the
AI loop needs — the CV runs server-side, so none of a framework's local image
matching is wanted here.

Text input: ASCII goes through ``input text``; anything else (Chinese, emoji,
``%``, control chars) is delivered via the ADBKeyboard IME's ``ADB_INPUT_B64``
broadcast, auto-installing the bundled APK on first use (GPL-2.0, vendored
with license + provenance in ``qirabot/assets/``).
"""

from __future__ import annotations

import base64
import io
import time
from typing import Any

from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig, split_combo
from qirabot.adb import AdbDevice
from qirabot.exceptions import QirabotError

# The ADBKeyboard IME (github.com/senzhk/ADBKeyBoard) receives text as a
# base64 broadcast, sidestepping `input text`'s ASCII-only, shell-quoting
# minefield for real-world text.
_ADB_IME_ID = "com.android.adbkeyboard/.AdbIME"
_ADB_IME_APK = "ADBKeyboard.apk"

# `input text` chokes on long strings on some OEM shells; chunk conservatively.
_TEXT_CHUNK = 300


def _ascii_typeable(text: str) -> bool:
    """True when ``input text`` can carry ``text`` verbatim.

    ``%`` is excluded because `input text` expands %s (and OEMs vary on other
    %-sequences); control chars (\\n, \\t) never survive the input pipeline.
    """
    return text.isascii() and "%" not in text and all(ch == " " or ch.isprintable() for ch in text)


def _shell_single_quote(arg: str) -> str:
    """Quote for the DEVICE-side sh (adb shell joins args into one command line)."""
    return "'" + arg.replace("'", "'\\''") + "'"


class AdbAdapter(DeviceAdapter):
    """Adapter for :class:`qirabot.adb.AdbDevice` targets."""

    # adb keyevent names for the keys the server may emit. `input keyevent`
    # accepts both bare names (ENTER) and KEYCODE_-prefixed ones; unknown keys
    # pass through uppercased.
    _KEY_MAP = {
        "enter": "ENTER",
        "return": "ENTER",
        "backspace": "KEYCODE_DEL",
        "delete": "KEYCODE_DEL",
        "back": "BACK",
        "home": "HOME",
        "menu": "MENU",
        "tab": "TAB",
        "space": "SPACE",
    }

    # `input` events return before the UI reacts; same fixed floor the other
    # device adapters use (see DeviceAdapter.settle_seconds).
    _SETTLE_SECONDS = 1.0

    # Delay between the focusing tap and the first keystroke, so focus
    # animations / IME activation finish before characters start arriving.
    _FOCUS_SETTLE = 0.3

    def __init__(self, target: Any) -> None:
        self._device: AdbDevice = target
        self._last_size: tuple[int, int] | None = None
        # IME bookkeeping: the user's keyboard is restored on close().
        self._saved_ime: str | None = None
        self._ime_ready = False

    @classmethod
    def accepts(cls, target: Any) -> bool:
        return isinstance(target, AdbDevice)

    @property
    def current_target(self) -> Any:
        return self._device

    # ---- screen -------------------------------------------------------------

    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        cfg = config or ScreenshotConfig()
        from PIL import Image

        # screencap can transiently return nothing (device busy, screen off
        # transition) or a truncated stream (exec-out cut mid-transfer — flaky
        # USB / WiFi adb — with exit code 0); retry a couple of times before
        # failing the step. Only a full pixel decode proves the frame is whole,
        # so decode here and reuse the result below.
        png = b""
        img: Image.Image | None = None
        for attempt in range(3):
            png = self._device.screencap()
            if png:
                try:
                    img = Image.open(io.BytesIO(png))
                    img.load()
                    break
                except OSError:
                    img = None
            time.sleep(0.15)
        if img is None:
            raise QirabotError(
                "adb screencap returned no usable frame after 3 attempts "
                + ("(output was truncated or undecodable)" if png else "(no data)"),
                code="adb.screencap_empty",
            )
        # screencap always returns an upright frame, so no rotation fixups
        # are needed for the size cache.
        self._last_size = (img.width, img.height)
        if cfg.format == "png":
            return png
        return self._encode_image(img, cfg)

    def device_info(self) -> DeviceInfo:
        # Prefer the last screenshot's dimensions so the reported size matches
        # the image the model sees (wm size doesn't track rotation).
        if self._last_size is not None:
            width, height = self._last_size
        else:
            width, height = self._device.wm_size()
        return DeviceInfo(platform="android", width=width, height=height)

    # ---- pointer ------------------------------------------------------------

    def click(self, x: float, y: float) -> None:
        self._device.shell(f"input tap {int(x)} {int(y)}")

    def double_click(self, x: float, y: float) -> None:
        # One shell round-trip: two `input tap` invocations back to back keep
        # the inter-tap gap inside double-tap detection (~300ms); two separate
        # adb round-trips would not.
        ix, iy = int(x), int(y)
        self._device.shell(f"input tap {ix} {iy} && input tap {ix} {iy}")

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        # A zero-distance swipe with a duration is the adb long-press idiom.
        ix, iy = int(x), int(y)
        ms = max(1, int(duration * 1000))
        self._device.shell(f"input swipe {ix} {iy} {ix} {iy} {ms}")

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        self._device.shell(f"input swipe {int(from_x)} {int(from_y)} {int(to_x)} {int(to_y)} 500")

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

    # ---- keys ---------------------------------------------------------------

    def press_key(self, key: str) -> None:
        # Android has no held-modifier concept over `input keyevent`; combos
        # degrade to the base key (same behavior as the retired adapter).
        _mods, base = split_combo(key)
        name = self._KEY_MAP.get(base.lower(), base.upper())
        self._device.shell(f"input keyevent {name}")

    def go_back(self) -> None:
        self._device.shell("input keyevent BACK")

    def clear_focused(self) -> None:
        # No element model over raw adb: best effort is caret-to-end plus a
        # burst of deletes. One shell round-trip (`input keyevent` accepts many
        # keycodes per invocation) instead of 64 (~2-5s of adb round-trips).
        try:
            self._device.shell("input keyevent KEYCODE_MOVE_END")
        except QirabotError:
            pass
        self._device.shell("input keyevent " + " ".join(["KEYCODE_DEL"] * 64))

    # ---- text ---------------------------------------------------------------

    def type_text(self, x: float, y: float, text: str) -> None:
        self.click(x, y)
        time.sleep(self._FOCUS_SETTLE)
        self.type_focused(text)

    def type_focused(self, text: str) -> None:
        if not text:
            return
        if _ascii_typeable(text):
            self._input_text(text)
        else:
            self._ime_text(text)

    def _input_text(self, text: str) -> None:
        for start in range(0, len(text), _TEXT_CHUNK):
            chunk = text[start : start + _TEXT_CHUNK]
            # `input text` renders %s as a space; single-quote the whole arg so
            # the device shell passes metacharacters ("&;<>$`\"") through.
            arg = _shell_single_quote(chunk.replace(" ", "%s"))
            self._device.shell(f"input text {arg}")

    def _ime_text(self, text: str) -> None:
        self._ensure_adb_ime()
        payload = base64.b64encode(text.encode("utf-8")).decode("ascii")
        self._device.shell(f"am broadcast -a ADB_INPUT_B64 --es msg {payload}")

    def _ensure_adb_ime(self) -> None:
        """Make ADBKeyboard the active IME (installing the bundled APK if needed)."""
        if self._ime_ready:
            return
        dev = self._device
        if _ADB_IME_ID not in dev.shell("ime list -s -a"):
            self._install_adb_ime()
        # Save the user's keyboard so close() can restore it.
        current = dev.shell("settings get secure default_input_method").strip()
        if current and current != "null" and current != _ADB_IME_ID:
            self._saved_ime = current
        dev.shell(f"ime enable {_ADB_IME_ID}")
        dev.shell(f"ime set {_ADB_IME_ID}")
        time.sleep(0.3)  # let the IME switch land before the first broadcast
        self._ime_ready = True

    def _install_adb_ime(self) -> None:
        from importlib import resources

        apk = resources.files("qirabot.assets").joinpath(_ADB_IME_APK)
        if not apk.is_file():
            raise QirabotError(
                "typing non-ASCII text needs the ADBKeyboard IME, and this "
                "build ships without the APK. Install it manually "
                "(https://github.com/senzhk/ADBKeyBoard) or use the Appium "
                "engine (--appium-url).",
                code="adb.ime_missing",
            )
        try:
            with resources.as_file(apk) as path:
                self._device.install(str(path))
        except QirabotError as e:
            raise QirabotError(
                f"could not install the ADBKeyboard IME ({e.message}). If app "
                "installs are blocked on this device (MDM policy), preinstall "
                "it manually or use the Appium engine (--appium-url).",
                code="adb.ime_install_failed",
            ) from e

    def close(self) -> None:
        # Best-effort: give the user their keyboard back.
        if self._saved_ime:
            try:
                self._device.shell(f"ime set {self._saved_ime}")
            except Exception:
                pass
            self._saved_ime = None
        self._ime_ready = False
