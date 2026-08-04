"""Window-bound Windows desktop adapter (scancode input for games).

Drives a :class:`~qirabot.windows.Window` with raw SendInput: mouse moves are
absolute virtual-desktop coordinates, keys are DirectInput scancodes (which
games poll; the virtual-key codes higher-level libraries send never reach
them). Screenshots are the window's client area, so model coordinates are
client-relative pixels.

Behavior deliberately preserved from the retired airtest Windows adapter:
click hardening (pre-move jitter + a real hold), modifier press→pace→base→
reverse-release ordering, per-key hold/gap pacing, and the focus settle before
typing. What airtest couldn't do — horizontal wheel, unicode text without a
SendKeys fallback — is native here. Text games can't receive as injected
unicode (CJK and friends: KEYEVENTF_UNICODE needs a TranslateMessage loop
Raw-Input/DirectInput games don't run) is delivered via clipboard + Ctrl+V;
QIRA_TEXT_FALLBACK=unicode reverts to injection.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from qirabot import windows as win
from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig, split_combo
from qirabot.windows import Window

logger = logging.getLogger("qirabot")

# ---------------------------------------------------------------------------
# Key tables (US layout — non-US layouts get the KEYEVENTF_UNICODE path)
# ---------------------------------------------------------------------------

# DirectInput (scan code set 1) make codes: name -> (scancode, extended).
SCANCODES: dict[str, tuple[int, bool]] = {
    "ESCAPE": (0x01, False),
    **{str(d): (0x02 + i, False) for i, d in enumerate("1234567890")},
    "-": (0x0C, False), "=": (0x0D, False),
    "BACKSPACE": (0x0E, False), "TAB": (0x0F, False),
    "Q": (0x10, False), "W": (0x11, False), "E": (0x12, False), "R": (0x13, False),
    "T": (0x14, False), "Y": (0x15, False), "U": (0x16, False), "I": (0x17, False),
    "O": (0x18, False), "P": (0x19, False), "[": (0x1A, False), "]": (0x1B, False),
    "ENTER": (0x1C, False), "LCTRL": (0x1D, False),
    "A": (0x1E, False), "S": (0x1F, False), "D": (0x20, False), "F": (0x21, False),
    "G": (0x22, False), "H": (0x23, False), "J": (0x24, False), "K": (0x25, False),
    "L": (0x26, False), ";": (0x27, False), "'": (0x28, False), "`": (0x29, False),
    "LSHIFT": (0x2A, False), "BACKSLASH": (0x2B, False),
    "Z": (0x2C, False), "X": (0x2D, False), "C": (0x2E, False), "V": (0x2F, False),
    "B": (0x30, False), "N": (0x31, False), "M": (0x32, False),
    ",": (0x33, False), ".": (0x34, False), "/": (0x35, False),
    "RSHIFT": (0x36, False), "*": (0x37, False), "LALT": (0x38, False),
    "SPACE": (0x39, False), "CAPS_LOCK": (0x3A, False),
    **{f"F{i}": (0x3A + i, False) for i in range(1, 11)},  # F1..F10 = 0x3B..0x44
    "NUM_LOCK": (0x45, False), "SCROLL_LOCK": (0x46, False),
    "NUMPAD_7": (0x47, False), "NUMPAD_8": (0x48, False), "NUMPAD_9": (0x49, False),
    "NUMPAD_-": (0x4A, False),
    "NUMPAD_4": (0x4B, False), "NUMPAD_5": (0x4C, False), "NUMPAD_6": (0x4D, False),
    "NUMPAD_+": (0x4E, False),
    "NUMPAD_1": (0x4F, False), "NUMPAD_2": (0x50, False), "NUMPAD_3": (0x51, False),
    "NUMPAD_0": (0x52, False), "NUMPAD_.": (0x53, False),
    "F11": (0x57, False), "F12": (0x58, False),
    # Extended keys (E0 prefix on the wire → KEYEVENTF_EXTENDEDKEY).
    "RCTRL": (0x1D, True), "RALT": (0x38, True),
    "NUMPAD_ENTER": (0x1C, True), "NUMPAD_/": (0x35, True),
    "HOME": (0x47, True), "UP": (0x48, True), "PAGE_UP": (0x49, True),
    "LEFT": (0x4B, True), "RIGHT": (0x4D, True),
    "END": (0x4F, True), "DOWN": (0x50, True), "PAGE_DOWN": (0x51, True),
    "INSERT": (0x52, True), "DELETE": (0x53, True),
    "LWINDOWS": (0x5B, True), "RWINDOWS": (0x5C, True), "MENU": (0x5D, True),
    "PRINT_SCREEN": (0x37, True),
}

# Wire key names -> scancode-table names. Unmapped single letters/digits pass
# through uppercased ("w" -> "W"); anything not in SCANCODES after that falls
# back to unicode injection.
WIN_KEY_ALIASES = {
    "enter": "ENTER", "return": "ENTER", "escape": "ESCAPE", "esc": "ESCAPE",
    "space": "SPACE", "tab": "TAB", "backspace": "BACKSPACE",
    "delete": "DELETE", "del": "DELETE",
    "shift": "LSHIFT", "ctrl": "LCTRL", "control": "LCTRL",
    "alt": "LALT", "option": "LALT",
    "win": "LWINDOWS", "super": "LWINDOWS", "meta": "LWINDOWS",
    "cmd": "LWINDOWS", "command": "LWINDOWS",
    "up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT",
    "arrowup": "UP", "arrowdown": "DOWN", "arrowleft": "LEFT", "arrowright": "RIGHT",
    "home": "HOME", "end": "END", "pageup": "PAGE_UP", "pagedown": "PAGE_DOWN",
    "pgup": "PAGE_UP", "pgdn": "PAGE_DOWN", "insert": "INSERT", "ins": "INSERT",
}

# US-layout ASCII -> scancode names for type_text. Shifted chars hold LSHIFT
# around the base key; plain symbols/space map directly.
SHIFT_CHARS = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "BACKSLASH", ":": ";", '"': "'", "~": "`", "<": ",", ">": ".", "?": "/",
}
SYMBOL_CHARS = {
    "-": "-", "=": "=", "[": "[", "]": "]", "\\": "BACKSLASH", ";": ";",
    "'": "'", "`": "`", ",": ",", ".": ".", "/": "/", " ": "SPACE",
}


def char_scancode(ch: str) -> tuple[bool, str] | None:
    """ASCII char -> (needs_shift, scancode name), or None if not typeable."""
    if "a" <= ch <= "z":
        return False, ch.upper()
    if "A" <= ch <= "Z":
        return True, ch
    if "0" <= ch <= "9":
        return False, ch
    if ch in SYMBOL_CHARS:
        return False, SYMBOL_CHARS[ch]
    if ch in SHIFT_CHARS:
        return True, SHIFT_CHARS[ch]
    return None


# Pacing for the SendInput paths. SendInput itself has zero delay, so an
# unpaced press→release is down for well under a millisecond — apps that poll
# keyboard state per frame (games at 60fps sample every ~16.7ms) skip the key
# entirely. ≥20ms of hold guarantees at least one frame samples the key as
# down; the inter-key gap keeps distinct keystrokes in distinct frames.
KEY_HOLD = 0.025
KEY_GAP = 0.025
# Delay between the focusing click and the first keystroke, so focus
# animations/IME activation finish before characters start arriving.
FOCUS_SETTLE = 0.3
# After Ctrl+V: the target reads the clipboard while processing the paste,
# possibly frames later — don't restore the user's clipboard under it.
PASTE_SETTLE = 0.2
# Click hardening: apps that hit-test on WM_MOUSEMOVE or debounce fast clicks
# miss teleport-and-instant-click; jitter the cursor and hold the button.
CLICK_OFFSET = 2
CLICK_DURATION = 0.1
# Pixels of intended travel per wheel notch (WHEEL_DELTA); approximate and
# deliberately generous — apps disagree on lines-per-notch anyway.
PIXELS_PER_NOTCH = 100
# Gap between per-notch wheel events. Games reading Raw Input count wheel
# EVENTS (often just their sign) rather than summing deltas, so notches must
# arrive as separate events in separate frames to all register.
WHEEL_NOTCH_GAP = 0.02


class WindowsAdapter(DeviceAdapter):
    """Adapter for :class:`qirabot.windows.Window` targets (Windows only)."""

    # Desktop UI (window transitions, scroll inertia) needs a generous floor.
    _SETTLE_SECONDS = 1.0

    # SendInput injects into the machine's real mouse/keyboard.
    controls_user_input = True

    # Games animate into their modifier "mode" (alt unlocking the cursor,
    # overlays fading in) over several frames; a click that arrives before the
    # transition finishes is processed as an unmodified click. Hold the
    # modifier well before the button goes down and keep it held after the
    # release so the frame that processes the click still samples it as down.
    _MODIFIER_LEAD = 0.35
    _MODIFIER_TAIL = 0.15

    def __init__(self, target: Any) -> None:
        self._window: Window = target
        self._last_size: tuple[int, int] | None = None
        # Held-input tracking so release_all_inputs() can sweep up a forgotten
        # release (see DeviceAdapter.release_all_inputs).
        self._held_keys: list[tuple[int, bool]] = []
        self._mouse_held = False
        # True when a key_down was injected while the window was NOT
        # foreground: the down event went to whichever window had focus, so
        # the next pointer action must re-press the held keys.
        self._held_undelivered = False
        self._ime_warned = False

    def _assert_english_ime(self) -> bool:
        """Before each injected keystroke batch: force the US English IME on
        the focused control. True when the scancode path is safe to use.

        An active CJK IME swallows injected scancode letters into its
        composition window instead of the game — part of the same "input must
        actually arrive" contract as ensure_foreground. IME state lives on the
        focused control's input context and is re-activated on every focus
        change, so this runs before every keystroke batch (after the window is
        fronted and the target control focused), never just once.
        Window(english_ime=False) opts out; best-effort because IME state must
        never block input — a False return steers text delivery to the paste
        path, which bypasses IME composition.
        """
        if not getattr(self._window, "english_ime", True):
            return True  # opted out: the caller manages IME state themselves
        try:
            if win.ensure_english_input(self._window.hwnd):
                return True
        except Exception:
            pass
        if not self._ime_warned:
            self._ime_warned = True
            logger.warning(
                "could not switch the window to the English IME; "
                "text will be delivered via the paste fallback"
            )
        return False

    @classmethod
    def accepts(cls, target: Any) -> bool:
        return isinstance(target, Window)

    @property
    def current_target(self) -> Any:
        return self._window

    # ---- screen -------------------------------------------------------------

    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        cfg = config or ScreenshotConfig()
        with win.dpi_awareness():
            img = win.capture_window(self._window.hwnd)
            if img is None:
                # GPU/DirectX window PrintWindow can't see: grab the desktop
                # and crop to the client rect (needs the window on top, which
                # game input requires anyway).
                win.ensure_foreground(self._window.hwnd)
                x, y, w, h = win.client_rect(self._window.hwnd)
                img = win.capture_screen_region(x, y, w, h)
        self._last_size = (img.width, img.height)
        return self._encode_image(img, cfg)

    def device_info(self) -> DeviceInfo:
        if self._last_size is not None:
            width, height = self._last_size
        else:
            with win.dpi_awareness():
                _, _, width, height = win.client_rect(self._window.hwnd)
        return DeviceInfo(platform="desktop", width=width, height=height)

    def window_info(self) -> dict[str, Any] | None:
        # Recorder window-following hook (recording.py resolves the region).
        try:
            return {"title": self._window.title, "hwnd": self._window.hwnd}
        except Exception:
            return None

    # ---- coordinates ----------------------------------------------------------

    def _to_absolute(self, x: float, y: float) -> tuple[int, int]:
        """Client-area pixels -> SendInput's 0..65535 virtual-desktop space.

        The virtual desktop's origin is negative when a monitor sits left of/
        above the primary — subtract it, don't assume (0, 0).
        """
        cx, cy, _, _ = win.client_rect(self._window.hwnd)
        vx, vy, vw, vh = win.virtual_screen()
        sx, sy = cx + int(x), cy + int(y)
        ax = round((sx - vx) * 65535 / max(vw - 1, 1))
        ay = round((sy - vy) * 65535 / max(vh - 1, 1))
        return ax, ay

    def _move_flags(self) -> int:
        return win.MOUSEEVENTF_MOVE | win.MOUSEEVENTF_ABSOLUTE | win.MOUSEEVENTF_VIRTUALDESK

    def _move_to(self, x: float, y: float) -> None:
        ax, ay = self._to_absolute(x, y)
        win.send_inputs([win.mouse_event(self._move_flags(), ax, ay)])

    # ---- pointer ------------------------------------------------------------

    def _reassert_held_keys(self) -> None:
        """Re-press every key held via key_down.

        Repairs the two ways a held modifier silently vanishes before a click
        lands: its key_down was injected while another window had focus (the
        down event went there), or ensure_foreground's ALT-tap unlock released
        a held ALT. Only call when delivery is actually in doubt — games that
        TOGGLE their modifier mode on every down (Raw Input carries no repeat
        flag to filter on) treat a duplicate down as "switch the mode back
        off", turning the modifier click back into a plain click.
        """
        if not self._held_keys:
            return
        win.send_inputs(
            [win.key_scancode_event(code, ext, False) for code, ext in self._held_keys]
        )
        # Frame-polling apps need to sample the modifiers as held before the
        # button event arrives.
        time.sleep(KEY_HOLD)
        self._held_undelivered = False

    def _prepare_pointer_action(self) -> None:
        """Front the window and repair held-key delivery — only when needed.

        Foreground already ours and every held key delivered there: do
        nothing, so the wire sequence stays modifier-down → click → up exactly
        once (toggle-mode games break on duplicate downs, see
        ``_reassert_held_keys``). Otherwise front the window (whose ALT-tap
        unlock may release a held ALT) and re-press whatever is held.
        """
        if win.is_foreground(self._window.hwnd):
            if self._held_undelivered:
                self._reassert_held_keys()
            return
        win.ensure_foreground(self._window.hwnd)
        if self._held_keys:
            self._reassert_held_keys()

    def _button_click(self, x: float, y: float, down: int, up: int) -> None:
        with win.dpi_awareness():
            self._prepare_pointer_action()
            # Hardening: approach moves before the press so hit-testing apps
            # see WM_MOUSEMOVE, then a real hold instead of a ~0ms blip.
            for ox, oy in ((-CLICK_OFFSET, -CLICK_OFFSET), (CLICK_OFFSET, CLICK_OFFSET), (0, 0)):
                self._move_to(x + ox, y + oy)
                time.sleep(0.01)
            win.send_inputs([win.mouse_event(down)])
            time.sleep(CLICK_DURATION)
            win.send_inputs([win.mouse_event(up)])

    def click(self, x: float, y: float) -> None:
        self._button_click(x, y, win.MOUSEEVENTF_LEFTDOWN, win.MOUSEEVENTF_LEFTUP)

    def right_click(self, x: float, y: float) -> None:
        self._button_click(x, y, win.MOUSEEVENTF_RIGHTDOWN, win.MOUSEEVENTF_RIGHTUP)

    def double_click(self, x: float, y: float) -> None:
        self.click(x, y)
        time.sleep(0.05)  # within the double-click interval, distinct events
        self.click(x, y)

    def hover(self, x: float, y: float) -> None:
        with win.dpi_awareness():
            self._move_to(x, y)

    def mouse_down(self, x: float, y: float) -> None:
        with win.dpi_awareness():
            self._prepare_pointer_action()
            self._move_to(x, y)
            win.send_inputs([win.mouse_event(win.MOUSEEVENTF_LEFTDOWN)])
        self._mouse_held = True

    def mouse_up(self, x: float | None = None, y: float | None = None) -> None:
        with win.dpi_awareness():
            if x is not None and y is not None:
                self._move_to(x, y)
            win.send_inputs([win.mouse_event(win.MOUSEEVENTF_LEFTUP)])
        self._mouse_held = False

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        with win.dpi_awareness():
            self._prepare_pointer_action()
            self._move_to(from_x, from_y)
            win.send_inputs([win.mouse_event(win.MOUSEEVENTF_LEFTDOWN)])
            time.sleep(0.05)
            # Interpolated moves so drop targets see the drag travel.
            steps = 10
            for i in range(1, steps + 1):
                self._move_to(
                    from_x + (to_x - from_x) * i / steps,
                    from_y + (to_y - from_y) * i / steps,
                )
                time.sleep(0.02)
            win.send_inputs([win.mouse_event(win.MOUSEEVENTF_LEFTUP)])

    # ---- scrolling ------------------------------------------------------------

    def scroll(self, x: float, y: float, direction: str, distance: int) -> None:
        self._scroll_pixels(x, y, direction, distance * 100)

    def _scroll_pixels(self, x: float, y: float, direction: str, pixels: int) -> None:
        # Anchor at the window center unless the wire resolved an element
        # (scroll_at); the wheel needs a real cursor position either way.
        info = self.device_info()
        self._wheel(
            x or info.width / 2.0, y or info.height / 2.0, direction, pixels
        )

    def _wheel(self, cx: float, cy: float, direction: str, pixels: int) -> None:
        if pixels <= 0:
            pixels = 300  # ~3 notches, a sensible default page-nudge
        notches = max(1, round(pixels / PIXELS_PER_NOTCH))
        with win.dpi_awareness():
            win.ensure_foreground(self._window.hwnd)
            self._move_to(cx, cy)  # wheel goes to the control under the cursor
            if direction in ("up", "down"):
                delta = win.WHEEL_DELTA * (1 if direction == "up" else -1)
                flags = win.MOUSEEVENTF_WHEEL
            else:
                # Horizontal wheel: positive = right (airtest couldn't do this).
                delta = win.WHEEL_DELTA * (1 if direction == "right" else -1)
                flags = win.MOUSEEVENTF_HWHEEL
            # One event per notch, paced across frames — a single merged
            # delta reads as ONE notch to games that count events instead of
            # summing (Raw Input/frame polling); regular apps sum either way.
            for i in range(notches):
                if i:
                    time.sleep(WHEEL_NOTCH_GAP)
                win.send_inputs([win.mouse_event(flags, data=delta)])

    # ---- keys ---------------------------------------------------------------

    def _scancode_for(self, key: str) -> tuple[int, bool] | None:
        name = WIN_KEY_ALIASES.get(key.lower(), key.upper())
        return SCANCODES.get(name)

    def _tap_scancode(self, code: int, extended: bool) -> None:
        win.send_inputs([win.key_scancode_event(code, extended, False)])
        time.sleep(KEY_HOLD)
        win.send_inputs([win.key_scancode_event(code, extended, True)])

    def key_down(self, key: str) -> None:
        sc = self._scancode_for(key)
        if sc is None:
            raise NotImplementedError(f"no scancode for key {key!r}")
        if not win.ensure_foreground(self._window.hwnd):
            # Keyboard input follows focus, not coordinates: this down event is
            # about to land in whatever window IS focused. Surface it — the
            # symptom otherwise is "clicks work but the modifier doesn't" —
            # and mark the held set undelivered so the next pointer action
            # re-presses it once the window is actually foreground.
            self._held_undelivered = True
            logger.warning(
                "key_down(%r): could not bring window %r to the foreground; "
                "the key press may go to another window",
                key, self._window.hwnd,
            )
        self._assert_english_ime()
        win.send_inputs([win.key_scancode_event(sc[0], sc[1], False)])
        self._held_keys.append(sc)

    def key_up(self, key: str) -> None:
        sc = self._scancode_for(key)
        if sc is None:
            raise NotImplementedError(f"no scancode for key {key!r}")
        win.send_inputs([win.key_scancode_event(sc[0], sc[1], True)])
        if sc in self._held_keys:
            self._held_keys.remove(sc)
        if not self._held_keys:
            self._held_undelivered = False

    def release_all_inputs(self) -> None:
        # Best-effort sweep; one stuck input must not block releasing the rest.
        for code, extended in reversed(self._held_keys):
            try:
                win.send_inputs([win.key_scancode_event(code, extended, True)])
            except Exception:
                pass
        self._held_keys.clear()
        self._held_undelivered = False
        if self._mouse_held:
            try:
                win.send_inputs([win.mouse_event(win.MOUSEEVENTF_LEFTUP)])
            except Exception:
                pass
            self._mouse_held = False

    def press_key(self, key: str) -> None:
        mods, base = split_combo(key)
        codes = [self._scancode_for(k) for k in mods + [base]]
        if all(c is not None for c in codes):
            win.ensure_foreground(self._window.hwnd)
            self._assert_english_ime()
            *mod_codes, base_code = codes
            # Hold mods around the base, release in reverse; pace with KEY_HOLD
            # so frame-polling apps sample the mods as held before the base
            # lands, and the base as down at all.
            for code, ext in mod_codes:  # type: ignore[misc]
                win.send_inputs([win.key_scancode_event(code, ext, False)])
            if mod_codes:
                time.sleep(KEY_HOLD)
            assert base_code is not None
            self._tap_scancode(*base_code)
            for code, ext in reversed(mod_codes):  # type: ignore[misc]
                win.send_inputs([win.key_scancode_event(code, ext, True)])
            return
        if not mods and len(base) == 1 and base.isprintable():
            # Outside the table (non-US char, exotic symbol): unicode injection.
            win.send_inputs(win.key_unicode_events(base, False))
            time.sleep(KEY_HOLD)
            win.send_inputs(win.key_unicode_events(base, True))
            return
        raise NotImplementedError(f"cannot press key {key!r} on Windows")

    # ---- text ---------------------------------------------------------------

    def type_text(self, x: float, y: float, text: str) -> None:
        self.click(x, y)
        time.sleep(FOCUS_SETTLE)
        self.type_focused(text)

    def type_focused(self, text: str) -> None:
        # Prefer scancodes so games receive the text; any unmappable char
        # (CJK, emoji, non-US symbols) — or a window that refuses to give up
        # its CJK IME — switches the WHOLE string to one fallback path so
        # ordering holds. The IME assert runs here, after the focusing click
        # has landed: IME state belongs to the focused control's input
        # context, so asserting any earlier is undone the moment the text box
        # takes focus.
        if not text:
            return
        codes = []
        for ch in text:
            m = char_scancode(ch)
            if m is None:
                break
            codes.append(m)
        win.ensure_foreground(self._window.hwnd)
        if len(codes) == len(text) and self._assert_english_ime():
            shift = SCANCODES["LSHIFT"]
            for needs_shift, name in codes:
                code, ext = SCANCODES[name]
                if needs_shift:
                    win.send_inputs([win.key_scancode_event(shift[0], shift[1], False)])
                self._tap_scancode(code, ext)
                if needs_shift:
                    win.send_inputs([win.key_scancode_event(shift[0], shift[1], True)])
                time.sleep(KEY_GAP)
            return
        if os.environ.get("QIRA_TEXT_FALLBACK", "").strip().lower() != "unicode":
            self._paste_text(text)
            return
        for ch in text:
            win.send_inputs(win.key_unicode_events(ch, False))
            time.sleep(KEY_HOLD)
            win.send_inputs(win.key_unicode_events(ch, True))
            time.sleep(KEY_GAP)

    def _paste_text(self, text: str) -> None:
        """Deliver text via clipboard + Ctrl+V — the IME-immune path.

        Used for unmappable text (CJK, emoji) and for ASCII when the window
        refused to drop its CJK IME (pasting bypasses IME composition, so the
        text arrives verbatim either way).

        KEYEVENTF_UNICODE injects VK_PACKET events that only become text if
        the target's message loop runs them through TranslateMessage →
        WM_CHAR; games on Raw Input (RIDEV_NOLEGACY) or DirectInput never see
        them. Ctrl+V rides the scancode path games DO receive. The previous
        clipboard text is restored afterwards (best effort — non-text
        contents like images are lost). QIRA_TEXT_FALLBACK=unicode reverts to
        unicode injection for targets that block pasting.
        """
        saved = win.get_clipboard_text()
        win.set_clipboard_text(text)
        try:
            self.press_key("ctrl+v")
            time.sleep(PASTE_SETTLE)
        finally:
            if saved is not None:
                try:
                    win.set_clipboard_text(saved)
                except Exception:
                    logger.warning("could not restore previous clipboard text")
