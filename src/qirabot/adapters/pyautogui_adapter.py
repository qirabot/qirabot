"""pyautogui module adapter for desktop automation."""

from __future__ import annotations

import platform
from typing import Any

from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig, split_combo

# Wire key names -> pyautogui key names. pyautogui's keys are lowercase and use
# short forms (esc, down, pageup), so the engine's CamelCase/Arrow* names must be
# translated or press()/hotkey() silently no-op on an unknown key name.
_PYAUTOGUI_KEYS = {
    "enter": "enter", "return": "enter", "escape": "esc", "esc": "esc",
    "backspace": "backspace", "delete": "delete", "del": "delete",
    "tab": "tab", "space": "space",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "pageup": "pageup", "pagedown": "pagedown", "home": "home", "end": "end",
}


class PyAutoGuiAdapter(DeviceAdapter):
    """Adapter for the pyautogui module itself (pass the module as target)."""

    def __init__(self, module: Any) -> None:
        self._pag = module
        self._scale: float | None = None
        # Held-input tracking for the split press/release primitives, so
        # release_all_inputs() can clean up anything mouse_down/key_down left
        # held (see DeviceAdapter.release_all_inputs).
        self._held_keys: set[str] = set()
        self._mouse_held = False

    @classmethod
    def accepts(cls, target: Any) -> bool:
        return getattr(target, "__name__", "") == "pyautogui" and hasattr(target, "screenshot")

    @property
    def current_target(self) -> Any:
        # Desktop has a single fixed target (the pyautogui module); it never
        # changes, so it is always returned as-is.
        return self._pag

    def _scale_factor(self) -> float:
        """Ratio of screenshot pixels to pyautogui's logical coordinate space.

        On macOS Retina displays ``pyautogui.screenshot()`` captures at the
        physical backing resolution (e.g. 2880x1800) while ``pyautogui.size()``
        and the mouse/click APIs operate in logical points (e.g. 1440x900). The
        AI returns coordinates in the screenshot's pixel space, so every action
        coordinate must be divided by this factor before being handed to
        pyautogui, otherwise clicks land at ``coord * scale`` (double on 2x).
        Cached after first probe; 1.0 on non-scaled displays.
        """
        if self._scale is None:
            logical = self._pag.size()
            shot = self._pag.screenshot()
            self._scale = shot.width / logical.width if logical.width else 1.0
        return self._scale

    def _to_logical(self, x: float, y: float) -> tuple[int, int]:
        s = self._scale_factor()
        return int(x / s), int(y / s)

    @staticmethod
    def _primary_modifier() -> str:
        """Platform select-all / paste modifier ('command' on macOS, else 'ctrl')."""
        return "command" if platform.system() == "Darwin" else "ctrl"

    def _paste_text(self, text: str) -> None:
        """Type text via the system clipboard.

        ``pyautogui.typewrite`` can only emit ASCII keystrokes, so any non-ASCII
        input (e.g. Chinese) must be pasted instead. pyperclip ships transitively
        with pyautogui (via mouseinfo) and is pinned in the ``desktop`` extra.
        """
        try:
            import pyperclip
        except ImportError as e:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "typing non-ASCII text requires pyperclip; install qirabot[desktop]"
            ) from e
        pyperclip.copy(text)
        self._pag.hotkey(self._primary_modifier(), "v")

    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        cfg = config or ScreenshotConfig()
        img = self._pag.screenshot()
        return self._encode_image(img, cfg)

    def click(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        self._pag.click(lx, ly)

    def double_click(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        self._pag.doubleClick(lx, ly)

    def right_click(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        self._pag.rightClick(lx, ly)

    def hover(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        self._pag.moveTo(lx, ly)

    def type_text(self, x: float, y: float, text: str) -> None:
        lx, ly = self._to_logical(x, y)
        self._pag.click(lx, ly)
        self.type_focused(text)

    def type_focused(self, text: str) -> None:
        # typewrite is ASCII-only; paste anything else (Chinese, emoji, …).
        if text.isascii():
            self._pag.typewrite(text, interval=0.02)
        else:
            self._paste_text(text)

    def clear_text(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        self._pag.click(lx, ly)
        self.clear_focused()

    def clear_focused(self) -> None:
        self._pag.hotkey(self._primary_modifier(), "a")
        self._pag.press("backspace")

    def _norm_key(self, k: str) -> str:
        kl = k.lower()
        if kl in ("ctrl", "control"):
            return "ctrl"
        if kl in ("alt", "option"):
            return "alt"
        if kl == "shift":
            return "shift"
        if kl in ("cmd", "command", "meta", "win", "super"):
            return "command" if platform.system() == "Darwin" else "win"
        return _PYAUTOGUI_KEYS.get(kl, kl)

    def press_key(self, key: str) -> None:
        # "ctrl+c" must go through hotkey() (press() takes a single key and
        # silently no-ops on a combo); a lone key goes through press().
        mods, base = split_combo(key)
        keys = [self._norm_key(m) for m in mods] + [self._norm_key(base)]
        if len(keys) > 1:
            self._pag.hotkey(*keys)
        else:
            self._pag.press(keys[0])

    def mouse_down(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        self._pag.mouseDown(lx, ly)
        self._mouse_held = True

    def mouse_up(self, x: float | None = None, y: float | None = None) -> None:
        if x is not None and y is not None:
            lx, ly = self._to_logical(x, y)
            self._pag.mouseUp(lx, ly)
        else:
            self._pag.mouseUp()
        self._mouse_held = False

    def key_down(self, key: str) -> None:
        k = self._norm_key(key)
        self._pag.keyDown(k)
        self._held_keys.add(k)

    def key_up(self, key: str) -> None:
        k = self._norm_key(key)
        self._pag.keyUp(k)
        self._held_keys.discard(k)

    def release_all_inputs(self) -> None:
        # Release in a best-effort sweep: one stuck key must not stop the rest
        # from being released. Keys first, then the mouse button.
        for k in list(self._held_keys):
            try:
                self._pag.keyUp(k)
            except Exception:
                pass
        self._held_keys.clear()
        if self._mouse_held:
            try:
                self._pag.mouseUp()
            except Exception:
                pass
            self._mouse_held = False

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        lfx, lfy = self._to_logical(from_x, from_y)
        ltx, lty = self._to_logical(to_x, to_y)
        self._pag.moveTo(lfx, lfy)
        # button="left" is REQUIRED, not cosmetic: pyautogui.drag() defaults to
        # button="primary" and, unlike mouseDown(), does NOT normalize it before
        # handing it to the macOS backend's _dragTo(), which asserts the button
        # is one of left/middle/right. So the default raises AssertionError on
        # macOS *after* the mouseDown fires (leaving the button stuck). Passing
        # "left" explicitly keeps the unnormalized "primary" from reaching _dragTo.
        self._pag.drag(ltx - lfx, lty - lfy, duration=0.5, button="left")

    @staticmethod
    def _wheel_clicks(notches: int) -> int:
        """Convert a notch count to ``pyautogui.scroll()``'s platform unit.

        ``pyautogui.scroll(clicks)`` means something different on each OS:

        * Windows: a raw wheel delta — ``dwData`` is passed straight to
          ``mouse_event``, and Windows treats ``WHEEL_DELTA`` (120) as one
          notch, so a notch needs ``clicks = 120``.
        * macOS: lines (``kCGScrollEventUnitLine``), ~3 lines per notch.
        * Linux/X11: one wheel "click" (button 4/5 press) per unit, i.e. a notch.

        Without this conversion the macOS-tuned ``distance * 3`` is only ~0.1
        notch on Windows (a sub-notch delta that most apps round to zero), which
        is why scrolling there barely moved.
        """
        system = platform.system()
        if system == "Windows":
            return notches * 120  # WHEEL_DELTA per notch
        if system == "Darwin":
            return notches * 3  # ~3 lines per notch (unchanged macOS behaviour)
        return notches  # X11: one button-4/5 click per notch

    def scroll(self, x: float, y: float, direction: str, distance: int) -> None:
        # The engine's plain `scroll` sends no x/y, so they arrive as 0 and the
        # scroll would anchor at the top-left corner (menu bar / non-scrollable
        # region) and do nothing. Fall back to the screen center, matching the
        # default anchor used elsewhere.
        if not x and not y:
            info = self.device_info()
            x, y = info.width / 2.0, info.height / 2.0
        lx, ly = self._to_logical(x, y)
        # On Windows pyautogui emits MOUSEEVENTF_WHEEL without a move, so its
        # x/y are ignored and the wheel lands on whatever is under the CURRENT
        # cursor; move there first so scroll_at actually targets (x, y). macOS
        # and X11 already move to the anchor inside their own _scroll.
        if platform.system() == "Windows":
            self._pag.moveTo(lx, ly)
        clicks = self._wheel_clicks(max(1, distance))
        if direction == "up":
            self._pag.scroll(clicks, x=lx, y=ly)
        elif direction == "down":
            self._pag.scroll(-clicks, x=lx, y=ly)
        elif direction == "left":
            self._pag.hscroll(-clicks, x=lx, y=ly)
        elif direction == "right":
            self._pag.hscroll(clicks, x=lx, y=ly)

    # Desktop UI (scroll inertia, window transitions) needs a generous floor; see
    # ``DeviceAdapter.settle_seconds`` for the rationale and override mechanism.
    _SETTLE_SECONDS = 1.0

    # pyautogui moves the machine's real mouse/keyboard.
    controls_user_input = True

    def device_info(self) -> DeviceInfo:
        # Report screenshot (physical) pixels, not pyautogui's logical points, so
        # the dimensions match the captured image and the screenshot-pixel
        # coordinate convention every adapter method consumes. Otherwise on a 2x
        # Retina display Qirabot.scroll()'s default center anchor (info.width / 2,
        # in logical points) would be divided by the scale factor a second time
        # inside _to_logical and land at a quarter position. Reuses the cached
        # scale probe; == 1.0 (unchanged) on non-scaled displays.
        s = self._scale_factor()
        size = self._pag.size()
        return DeviceInfo(
            platform="desktop",
            width=int(size.width * s),
            height=int(size.height * s),
        )
