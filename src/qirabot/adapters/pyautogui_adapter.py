"""pyautogui module adapter for desktop automation."""

from __future__ import annotations

import importlib
import logging
import os
import platform
import subprocess
import sys
import tempfile
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig, split_combo
from qirabot.displays import Display, supports_multi_display
from qirabot.exceptions import QirabotError

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

logger = logging.getLogger("qirabot")


def _screencapture(flags: list[str]) -> Any:
    """Run ``screencapture -x <flags> <tmp.png>`` and load the result.

    Raises CalledProcessError (with captured stderr) on a non-zero exit.
    """
    from PIL import Image

    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        subprocess.run(
            ["screencapture", "-x", *flags, path], check=True, capture_output=True
        )
        img = Image.open(path)
        img.load()
        return img
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _cg_display_image(display_id: int) -> Any:
    """In-process capture of one display via CoreGraphics, or None.

    Only used when macOS CONFIRMS Screen Recording access: without it,
    CGDisplayCreateImage doesn't fail — it returns the bare wallpaper with
    every window stripped, which would silently blind the bot. Returns None
    when the API is unavailable (removed on some macOS releases) or yields
    nothing.
    """
    if not display_id or _macos_capture_permitted() is not True:
        return None
    quartz: Any = importlib.import_module("Quartz")
    from PIL import Image

    cg = quartz.CGDisplayCreateImage(display_id)
    if cg is None:
        return None
    w, h = quartz.CGImageGetWidth(cg), quartz.CGImageGetHeight(cg)
    stride = quartz.CGImageGetBytesPerRow(cg)
    data = quartz.CGDataProviderCopyData(quartz.CGImageGetDataProvider(cg))
    return Image.frombuffer("RGBA", (w, h), bytes(data), "raw", "BGRA", stride, 1).convert("RGB")


def _macos_capture_permitted() -> bool | None:
    """Whether macOS reports Screen Recording access for this process
    (CGPreflightScreenCaptureAccess, 10.15+); None when it can't be asked."""
    try:
        quartz: Any = importlib.import_module("Quartz")
        return bool(quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def _stderr_text(e: subprocess.CalledProcessError) -> str:
    err = e.stderr or b""
    if isinstance(err, bytes):
        err = err.decode("utf-8", "replace")
    return " ".join(str(err).split())


class PyAutoGuiAdapter(DeviceAdapter):
    """Adapter for the pyautogui module itself (pass the module as target).

    Drives the primary display by default. :meth:`use_display` (wired from
    ``Qirabot(display=N)``) retargets it to another display: screenshots
    capture only that display and every action coordinate — still relative
    to the screenshot, as the model sees it — is shifted by the display's
    origin before it reaches pyautogui.
    """

    def __init__(self, module: Any, display: Display | None = None) -> None:
        self._pag = module
        self._scale: float | None = None
        self._display: Display | None = None
        # macOS display-capture strategy that last worked (see
        # _grab_display_macos); None = not probed yet.
        self._mac_strategy: str | None = None
        # Held-input tracking for the split press/release primitives, so
        # release_all_inputs() can clean up anything mouse_down/key_down left
        # held (see DeviceAdapter.release_all_inputs).
        self._held_keys: set[str] = set()
        self._mouse_held = False
        if display is not None:
            self.use_display(display)

    @classmethod
    def accepts(cls, target: Any) -> bool:
        return getattr(target, "__name__", "") == "pyautogui" and hasattr(target, "screenshot")

    @property
    def current_target(self) -> Any:
        # Desktop has a single fixed target (the pyautogui module); it never
        # changes, so it is always returned as-is.
        return self._pag

    @property
    def display(self) -> Display | None:
        """The display this adapter drives (``None`` = primary, legacy path)."""
        return self._display

    def use_display(self, display: Display) -> None:
        """Drive ``display`` instead of the primary. Linux only has display 1."""
        if display.index != 1 and not supports_multi_display():
            raise QirabotError(
                f"display {display.index}: selecting a display is supported on "
                "macOS and Windows only; on this platform the desktop backend "
                "drives display 1",
                code="display.unsupported",
            )
        self._display = display
        self._scale = None  # re-probe against the new display

    def _input_ctx(self) -> AbstractContextManager[Any]:
        """Context for coordinate-carrying pyautogui calls.

        Windows display rects are physical pixels measured per-monitor-DPI
        aware; pyautogui's process is only system-DPI aware, whose
        coordinates are virtualized on a monitor with a different scale.
        Running SetCursorPos & co. under the same thread DPI context keeps
        both in one space. Everything else needs no context.
        """
        if self._display is not None and sys.platform == "win32":
            from qirabot.windows import dpi_awareness

            return dpi_awareness()
        return nullcontext()

    def _logical_size(self) -> tuple[float, float]:
        """Size of the driven display in input coordinates."""
        if self._display is not None:
            return float(self._display.width), float(self._display.height)
        size = self._pag.size()
        return float(size.width), float(size.height)

    def _scale_factor(self) -> float:
        """Ratio of screenshot pixels to pyautogui's logical coordinate space.

        On macOS Retina displays ``pyautogui.screenshot()`` captures at the
        physical backing resolution (e.g. 2880x1800) while ``pyautogui.size()``
        and the mouse/click APIs operate in logical points (e.g. 1440x900). The
        AI returns coordinates in the screenshot's pixel space, so every action
        coordinate must be divided by this factor before being handed to
        pyautogui, otherwise clicks land at ``coord * scale`` (double on 2x).
        Cached after first probe; 1.0 on non-scaled displays (and always on
        a selected Windows display, where both sides are physical pixels).
        """
        if self._scale is None:
            width, _ = self._logical_size()
            shot = self._grab()
            self._scale = shot.width / width if width else 1.0
        return self._scale

    def _to_logical(self, x: float, y: float) -> tuple[int, int]:
        s = self._scale_factor()
        ox, oy = (self._display.x, self._display.y) if self._display else (0, 0)
        return int(x / s) + ox, int(y / s) + oy

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

    def _grab(self) -> Any:
        """Capture the screen, turning macOS's permission refusal into a fix.

        On macOS pyautogui shells out to ``screencapture``; without the
        Screen Recording permission it exits 1 with "could not create image
        from display", which surfaces as a bare CalledProcessError naming a
        temp file — nothing in it points at the actual cause. The same exit
        also happens for reasons that are NOT permission (e.g. a capture
        flag the OS version rejects), so the permission hint is only given
        when macOS confirms the permission is missing.
        """
        try:
            if self._display is None:
                return self._pag.screenshot()
            return self._grab_display(self._display)
        except subprocess.CalledProcessError as e:
            if platform.system() != "Darwin":
                raise
            if _macos_capture_permitted() is not False:
                detail = _stderr_text(e)
                raise RuntimeError(
                    f"screen capture failed ({' '.join(map(str, e.cmd))!s} exit "
                    f"{e.returncode}{': ' + detail if detail else ''}) even though "
                    "Screen Recording permission is granted"
                ) from e
            raise RuntimeError(
                "macOS refused the screen capture — the app running qirabot "
                "(Terminal, iTerm, your IDE, ...) lacks Screen Recording "
                "permission. Grant it in System Settings > Privacy & Security "
                "> Screen Recording, then fully quit and reopen that app."
            ) from e

    def _grab_display(self, display: Display) -> Any:
        """Capture exactly ``display``.

        macOS: ``screencapture -R`` over the display's rect in global points
        (the same mechanism Pillow's bbox grab uses; output is at native
        pixel density), falling back to ``-D<n>`` and then to CoreGraphics'
        CGDisplayCreateImage — screencapture's flag support varies across
        macOS releases. The first strategy that works is remembered.
        Windows: a BitBlt of the display's physical-pixel rect. Linux:
        display 1 only (see :meth:`use_display`), i.e. pyautogui's own
        capture.
        """
        if sys.platform == "darwin":
            return self._grab_display_macos(display)
        if sys.platform == "win32":
            from qirabot.windows import capture_screen_region, dpi_awareness

            with dpi_awareness():
                return capture_screen_region(
                    display.x, display.y, display.width, display.height
                )
        return self._pag.screenshot()

    def _grab_display_macos(self, display: Display) -> Any:
        rect = f"{display.x},{display.y},{display.width},{display.height}"
        strategies: list[tuple[str, Callable[[], Any]]] = [
            ("region", lambda: _screencapture(["-R", rect])),
            ("display", lambda: _screencapture([f"-D{display.index}"])),
            ("coregraphics", lambda: _cg_display_image(display.id)),
        ]
        if self._mac_strategy is not None:
            strategies.sort(key=lambda s: s[0] != self._mac_strategy)
        first_error: subprocess.CalledProcessError | None = None
        for name, run in strategies:
            try:
                img = run()
            except subprocess.CalledProcessError as e:
                first_error = first_error or e
                continue
            except Exception:
                logger.debug("display capture strategy %s failed", name, exc_info=True)
                continue
            if img is not None:
                self._mac_strategy = name
                return img
        if first_error is not None:
            raise first_error
        raise RuntimeError(f"could not capture display {display.index}")

    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        cfg = config or ScreenshotConfig()
        img = self._grab()
        return self._encode_image(img, cfg)

    def click(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        with self._input_ctx():
            self._pag.click(lx, ly)

    def double_click(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        with self._input_ctx():
            self._pag.doubleClick(lx, ly)

    def right_click(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        with self._input_ctx():
            self._pag.rightClick(lx, ly)

    def hover(self, x: float, y: float) -> None:
        lx, ly = self._to_logical(x, y)
        with self._input_ctx():
            self._pag.moveTo(lx, ly)

    def type_text(self, x: float, y: float, text: str) -> None:
        lx, ly = self._to_logical(x, y)
        with self._input_ctx():
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
        with self._input_ctx():
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
        with self._input_ctx():
            self._pag.mouseDown(lx, ly)
        self._mouse_held = True

    def mouse_up(self, x: float | None = None, y: float | None = None) -> None:
        if x is not None and y is not None:
            lx, ly = self._to_logical(x, y)
            with self._input_ctx():
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
        with self._input_ctx():
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
        clicks = self._wheel_clicks(max(1, distance))
        with self._input_ctx():
            # On Windows pyautogui emits MOUSEEVENTF_WHEEL without a move, so its
            # x/y are ignored and the wheel lands on whatever is under the CURRENT
            # cursor; move there first so scroll_at actually targets (x, y). macOS
            # and X11 already move to the anchor inside their own _scroll.
            if platform.system() == "Windows":
                self._pag.moveTo(lx, ly)
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
        # scale probe; == 1.0 (unchanged) on non-scaled displays. With a
        # selected display these are that display's dimensions.
        s = self._scale_factor()
        width, height = self._logical_size()
        return DeviceInfo(
            platform="desktop",
            width=int(width * s),
            height=int(height * s),
        )
