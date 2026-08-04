"""Zero-dependency Win32 layer for the window-bound desktop backend.

:class:`Window` is the SDK-level target for driving one Windows window::

    bot.ai(Window(title_re="Genshin"), "...")

Everything here is stdlib ``ctypes`` against user32/gdi32 — no pywinauto, no
pip dependencies. The DLLs are reached exclusively through the :func:`_user32`
/ :func:`_gdi32` shims so tests (and non-Windows imports) can substitute
fakes; nothing in this module touches a DLL at import time.

The adapter-facing surface is deliberately small: window resolution, the
client-area rectangle, two capture paths (PrintWindow, full-screen BitBlt
crop), a DPI-awareness guard, foreground management, SendInput event
builders (scancode, unicode, mouse) + :func:`send_inputs`, and clipboard
text access (the adapter's paste path for text games can't receive as
injected unicode).
"""

from __future__ import annotations

import ctypes
import sys
import time
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Iterator, Literal

from qirabot.exceptions import QirabotError

if TYPE_CHECKING:
    from PIL import Image

# ---------------------------------------------------------------------------
# DLL shims (the single mock point for tests; lazy so import works anywhere)
# ---------------------------------------------------------------------------

_dlls: dict[str, Any] = {}


def _dll(name: str) -> Any:
    if sys.platform != "win32":
        raise QirabotError(
            "the Windows window backend only runs on Windows",
            code="windows.unsupported_platform",
        )
    if name not in _dlls:
        _dlls[name] = ctypes.WinDLL(name, use_last_error=True)
    return _dlls[name]


def _user32() -> Any:
    return _dll("user32")


def _gdi32() -> Any:
    return _dll("gdi32")


def _kernel32() -> Any:
    return _dll("kernel32")


# ---------------------------------------------------------------------------
# DPI awareness (shared with recording.window_region)
# ---------------------------------------------------------------------------

# Sentinel HANDLE for SetThreadDpiAwarenessContext; -4 = PER_MONITOR_AWARE_V2.
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4


@contextmanager
def dpi_awareness() -> Iterator[None]:
    """Run the block with the thread per-monitor-DPI-aware (best-effort).

    Win32 coordinate/geometry APIs answer in the thread's DPI space; without
    this a DPI-virtualized process gets scaled-and-lying client rects, so
    clicks and captures drift on >100% displays. Thread-scoped and restored on
    exit so the host process's awareness is never permanently changed
    (pre-1607 Windows lacks the API — then it's a no-op).
    """
    prev = None
    user32 = _user32()
    try:
        try:
            fn = user32.SetThreadDpiAwarenessContext
            try:
                # DPI_AWARENESS_CONTEXT is a pointer-sized HANDLE. Without
                # explicit types ctypes passes -4 as a 32-bit int, the call
                # fails with ERROR_INVALID_PARAMETER and returns NULL — the
                # guard silently no-ops and every rect comes back
                # DPI-virtualized (wrong-region crops on >100% displays).
                fn.restype = ctypes.c_void_p
                fn.argtypes = [ctypes.c_void_p]
            except Exception:
                pass  # test fakes: bound methods reject attribute assignment
            prev = fn(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
        except Exception:
            prev = None
        yield
    finally:
        if prev:
            try:
                user32.SetThreadDpiAwarenessContext(prev)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Window handle
# ---------------------------------------------------------------------------


# WINFUNCTYPE only exists on Windows; CFUNCTYPE is ABI-identical on 64-bit and
# only used off-Windows by tests exercising the enumeration logic with fakes.
_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


def list_visible_windows() -> list[tuple[int, str]]:
    """All visible top-level windows with a non-empty title, as (hwnd, title)."""
    user32 = _user32()
    results: list[tuple[int, str]] = []

    @_WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)  # type: ignore[untyped-decorator]
    def enum_proc(hwnd: Any, _lparam: Any) -> int:
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value:
                    results.append((int(hwnd) if hwnd else 0, buf.value))
        return 1  # continue enumeration

    user32.EnumWindows(enum_proc, 0)
    return results


def _own_console_hwnd() -> int:
    """This process's console window handle, or 0 (no console / off-Windows).

    The console hosting a ``qirabot`` invocation echoes the full command line
    into its own window title — including the --window-title/title_re= value —
    so the console itself matches whatever pattern the user just typed. Title
    and class resolution exclude it; an explicit ``hwnd=`` can still reach it.
    Covers classic conhost only: under Windows Terminal this is the hidden
    ConPTY window, and the visible terminal is caught by the console-title +
    ancestor-process check in :meth:`Window._resolve` instead.
    """
    try:
        fn = _kernel32().GetConsoleWindow
        try:
            # HWNDs are pointer-sized; the ctypes default c_int restype would
            # sign-extend, breaking equality with enumerated (unsigned) hwnds.
            fn.restype = ctypes.c_void_p
        except Exception:
            pass  # test fakes: bound methods reject attribute assignment
        return int(fn() or 0)
    except Exception:
        return 0


def _console_title() -> str:
    """Title of the console this process is attached to ("" if none).

    Under Windows Terminal the visible terminal window mirrors this string as
    its own title, which is how _resolve recognizes it — GetConsoleWindow()
    only reaches the hidden ConPTY host there.
    """
    try:
        buf = ctypes.create_unicode_buffer(1024)
        if _kernel32().GetConsoleTitleW(buf, 1024):
            return buf.value
        return ""
    except Exception:
        return ""


def _window_pid(hwnd: int) -> int:
    """Owner process id of ``hwnd`` (0 on failure)."""
    try:
        pid = ctypes.c_ulong(0)
        _user32().GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
        return int(pid.value)
    except Exception:
        return 0


def _window_class(hwnd: int) -> str:
    """Window class name of ``hwnd`` ("" on failure)."""
    try:
        buf = ctypes.create_unicode_buffer(256)
        _user32().GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
        return buf.value
    except Exception:
        return ""


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.c_ulong),
        ("cntUsage", ctypes.c_ulong),
        ("th32ProcessID", ctypes.c_ulong),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", ctypes.c_ulong),
        ("cntThreads", ctypes.c_ulong),
        ("th32ParentProcessID", ctypes.c_ulong),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", ctypes.c_ulong),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


def _ancestor_pids() -> set[int]:
    """PIDs of this process and its ancestors (empty set on any failure).

    Walked via a Toolhelp snapshot: qirabot → cmd/powershell →
    WindowsTerminal. Identifies the terminal window hosting this run even
    though it lives in a foreign process. Depth-capped against ppid cycles
    (PID reuse can make the chain loop).
    """
    try:
        import os

        k32 = _kernel32()
        fn = k32.CreateToolhelp32Snapshot
        try:
            fn.restype = ctypes.c_void_p  # HANDLE; c_int would truncate
        except Exception:
            pass
        snap = fn(0x00000002, 0)  # TH32CS_SNAPPROCESS
        if not snap or snap == ctypes.c_void_p(-1).value:  # INVALID_HANDLE_VALUE
            return set()
        try:
            ppid: dict[int, int] = {}
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
            ok = k32.Process32FirstW(ctypes.c_void_p(snap), ctypes.byref(entry))
            while ok:
                ppid[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = k32.Process32NextW(ctypes.c_void_p(snap), ctypes.byref(entry))
        finally:
            k32.CloseHandle(ctypes.c_void_p(snap))
        pids: set[int] = set()
        pid = os.getpid()
        while pid and pid not in pids and len(pids) < 64:
            pids.add(pid)
            pid = ppid.get(pid, 0)
        pids.discard(0)
        return pids
    except Exception:
        return set()


def _visible_windows_by_class(class_name: str) -> list[tuple[int, str]]:
    """Visible top-level windows whose class equals ``class_name``, as
    (hwnd, title). Unlike :func:`list_visible_windows`, untitled windows are
    included — a game's renderer window may not have set its title yet."""
    user32 = _user32()
    results: list[tuple[int, str]] = []

    @_WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)  # type: ignore[untyped-decorator]
    def enum_proc(hwnd: Any, _lparam: Any) -> int:
        if user32.IsWindowVisible(hwnd):
            cbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cbuf, 256)
            if cbuf.value == class_name:
                length = user32.GetWindowTextLengthW(hwnd)
                title = ""
                if length:
                    tbuf = ctypes.create_unicode_buffer(length + 1)
                    user32.GetWindowTextW(hwnd, tbuf, length + 1)
                    title = tbuf.value
                results.append((int(hwnd) if hwnd else 0, title))
        return 1

    user32.EnumWindows(enum_proc, 0)
    return results


class Window:
    """Handle to one Windows top-level window.

    Args:
        hwnd: explicit window handle (from Spy++, ``qirabot doctor``, or a
            previous resolution).
        title: literal substring matched against visible window titles
            (regex metacharacters like ``(`` are safe here).
        title_re: regex matched (``re.search``) against visible window titles.
        class_name: exact window class name (e.g. Unity games expose
            ``UnityWndClass``, Unreal ``UnrealWindow``). More reliable than
            titles for games: it can't match a File Explorer window that
            shares the game's name, and it works before the game sets its
            title. Combinable with ``title``/``title_re`` to narrow further.
        ambiguous: what to do when several windows match:
            ``"error"`` (default) raises listing the candidates;
            ``"largest"`` picks the window with the biggest on-screen area —
            the right call for games whose launcher/overlay windows share the
            main window's exact title.
        timeout: seconds to keep polling (1s interval) for a matching window
            before giving up — for binding to a game that is still starting.
            0 (default) resolves exactly once.
        english_ime: switch the focused control's input language to US
            English before each injected keystroke batch (default True). An
            active CJK IME swallows injected letter keys into its composition
            window instead of the game, and IME state is re-activated per
            focused control — so the switch is re-asserted before every
            typing/keypress call, not once. When a window refuses the switch,
            text falls back to clipboard paste, which bypasses the IME. Pass
            False to leave the window's IME alone.

    ``hwnd`` alone, or any combination of ``title``/``title_re`` (mutually
    exclusive) and ``class_name``, selects the window. Whole-desktop
    automation belongs to the pyautogui backend instead.

    The console/terminal window hosting this process is never a title/class
    candidate: it echoes the qirabot command line — pattern included — into
    its own title and would otherwise self-match. Both hosts are covered:
    classic conhost (the console window itself) and Windows Terminal (a
    foreign-process window recognized by mirroring this console's title while
    belonging to an ancestor process). Pass ``hwnd=`` to target it
    deliberately.
    """

    def __init__(
        self,
        hwnd: int | None = None,
        title: str | None = None,
        title_re: str | None = None,
        class_name: str | None = None,
        ambiguous: Literal["error", "largest"] = "error",
        timeout: float = 0.0,
        english_ime: bool = True,
    ) -> None:
        if not hwnd and not title and not title_re and not class_name:
            raise QirabotError(
                "Window needs hwnd=, title=, title_re= or class_name= (for "
                "whole-desktop automation use the pyautogui backend)",
                code="windows.window_unspecified",
            )
        if title and title_re:
            raise QirabotError(
                "pass title= (literal substring) or title_re= (regex), not both",
                code="windows.window_unspecified",
            )
        if ambiguous not in ("error", "largest"):
            raise QirabotError(
                f"ambiguous= must be 'error' or 'largest', got {ambiguous!r}",
                code="windows.bad_argument",
            )
        self._hwnd = int(hwnd) if hwnd else None
        self._title = title
        self._title_re = title_re
        self._class_name = class_name
        self._ambiguous = ambiguous
        self._timeout = timeout
        self.english_ime = english_ime

    def __repr__(self) -> str:
        return (
            f"Window(hwnd={self._hwnd!r}, title={self._title!r}, "
            f"title_re={self._title_re!r}, class_name={self._class_name!r})"
        )

    @property
    def hwnd(self) -> int:
        """The resolved window handle (resolves ``title_re`` on first use)."""
        if self._hwnd is None:
            self._hwnd = self._resolve()
        return self._hwnd

    @property
    def title(self) -> str | None:
        user32 = _user32()
        length = user32.GetWindowTextLengthW(self.hwnd)
        if not length:
            return None
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(self.hwnd, buf, length + 1)
        return buf.value or None

    def _resolve(self) -> int:
        import re

        pattern = None
        wanted_parts = []
        if self._title is not None:
            pattern = re.compile(re.escape(self._title))
            wanted_parts.append(f"title {self._title!r}")
        elif self._title_re is not None:
            pattern = re.compile(self._title_re)
            wanted_parts.append(f"title_re {self._title_re!r}")
        if self._class_name is not None:
            wanted_parts.insert(0, f"class {self._class_name!r}")
        wanted = " + ".join(wanted_parts)

        console_hwnd = _own_console_hwnd()
        console_title = _console_title()
        ancestors: set[int] | None = None

        def own_terminal(h: int, t: str) -> bool:
            # The window this run's console lives in. Classic conhost: the
            # console window itself (hwnd match). Windows Terminal (Win11's
            # default host, even for plain cmd.exe): GetConsoleWindow() only
            # reaches the hidden ConPTY host, but the visible terminal mirrors
            # our console title — require its owner to also be an ancestor
            # process (qirabot → cmd → WindowsTerminal), so an unrelated
            # window that merely shares the title is never excluded.
            nonlocal ancestors
            if h == console_hwnd:
                return True
            if not console_title or t != console_title:
                return False
            # A console-host window whose title equals our console title IS
            # our console: classic conhost (ConsoleWindowClass), or Windows
            # Terminal (CASCADIA_HOSTING_WINDOW_CLASS) — the latter covers
            # Win11's default-terminal delegation, where WT adopts a console
            # it didn't spawn: no WT_SESSION in our env, WT not among our
            # ancestors, and GetConsoleWindow() pointing at the hidden ConPTY
            # host instead of the visible window. The echoed title embeds
            # this exact command line, so a foreign console sharing it is not
            # a realistic collision.
            if _window_class(h) in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"):
                return True
            if ancestors is None:
                ancestors = _ancestor_pids()
            return _window_pid(h) in ancestors

        deadline = time.monotonic() + self._timeout
        while True:
            if self._class_name is not None:
                windows = _visible_windows_by_class(self._class_name)
            else:
                windows = list_visible_windows()
            matches = [
                (h, t)
                for h, t in windows
                if (pattern is None or pattern.search(t)) and not own_terminal(h, t)
            ]
            if matches:
                break
            if time.monotonic() >= deadline:
                titles = ", ".join(repr(t) for _, t in list_visible_windows()[:20]) or "none"
                waited = f" within {self._timeout:g}s" if self._timeout > 0 else ""
                raise QirabotError(
                    f"no visible window matches {wanted}{waited} "
                    f"(visible windows: {titles})",
                    code="windows.window_not_found",
                )
            time.sleep(1.0)
        if len(matches) == 1:
            return matches[0][0]
        if self._ambiguous == "largest":
            return max(matches, key=lambda m: _window_area(m[0]))[0]
        listing = ", ".join(f"{t!r} (hwnd={h})" for h, t in matches[:10])
        raise QirabotError(
            f"{len(matches)} windows match {wanted}: {listing} — narrow the "
            "pattern, pass hwnd= (CLI: --hwnd), or pass ambiguous='largest' "
            "(CLI: --ambiguous largest) to pick the biggest matching window",
            code="windows.window_ambiguous",
        )


def _window_area(hwnd: int) -> int:
    """On-screen area of ``hwnd``'s window rect (0 on failure).

    Only used to ORDER candidate windows in the ``ambiguous="largest"``
    tiebreak, so DPI virtualization scaling the values doesn't matter.
    """
    rect = _RECT()
    if not _user32().GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        return 0
    return max(0, int(rect.right) - int(rect.left)) * max(0, int(rect.bottom) - int(rect.top))


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    """Client area of ``hwnd`` as screen-space ``(x, y, w, h)`` physical pixels.

    Call under :func:`dpi_awareness`. The client area is what the adapter
    screenshots, so model coordinates are client-relative pixels.
    """
    user32 = _user32()
    rect = _RECT()
    if not user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(rect)):
        raise QirabotError(
            f"GetClientRect failed for hwnd={hwnd} (window closed?)",
            code="windows.window_gone",
        )
    origin = _POINT(0, 0)
    user32.ClientToScreen(ctypes.c_void_p(hwnd), ctypes.byref(origin))
    return origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top


# GetSystemMetrics indices for the virtual desktop (all monitors' bounding box).
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79


def virtual_screen() -> tuple[int, int, int, int]:
    """Virtual-desktop bounds ``(x, y, w, h)`` — the origin can be NEGATIVE
    (a monitor left of / above the primary), which the normalization in the
    adapter must subtract, not clamp."""
    user32 = _user32()
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------

_PW_RENDERFULLCONTENT = 0x00000002  # DWM-composited content (Win 8.1+)
_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0
_BI_RGB = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


def _bitmap_to_image(mem_dc: Any, bitmap: Any, w: int, h: int) -> "Image.Image":
    """Read a selected 32-bit bitmap out of ``mem_dc`` into a PIL image."""
    from PIL import Image

    gdi32 = _gdi32()
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = w
    info.bmiHeader.biHeight = -h  # negative = top-down rows
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = _BI_RGB
    buf = ctypes.create_string_buffer(w * h * 4)
    if not gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf, ctypes.byref(info), _DIB_RGB_COLORS):
        raise QirabotError("GetDIBits failed", code="windows.capture_failed")
    return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)


@contextmanager
def _mem_bitmap(src_dc: Any, w: int, h: int) -> Iterator[tuple[Any, Any]]:
    """A memory DC with a ``w``x``h`` bitmap selected, cleaned up on exit."""
    gdi32 = _gdi32()
    mem_dc = gdi32.CreateCompatibleDC(src_dc)
    bitmap = gdi32.CreateCompatibleBitmap(src_dc, w, h)
    try:
        gdi32.SelectObject(mem_dc, bitmap)
        yield mem_dc, bitmap
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)


def capture_window(hwnd: int) -> "Image.Image | None":
    """Capture ``hwnd``'s client area via PrintWindow, or None if it failed.

    PW_RENDERFULLCONTENT asks DWM for composited content, which covers most
    modern apps even when occluded. DirectX-exclusive windows still come back
    black/failed — the caller falls back to :func:`capture_screen_region`.
    """
    user32, _ = _user32(), _gdi32()
    _, _, w, h = client_rect(hwnd)
    if w <= 0 or h <= 0:
        return None
    window_dc = user32.GetWindowDC(ctypes.c_void_p(hwnd))
    if not window_dc:
        return None
    try:
        with _mem_bitmap(window_dc, w, h) as (mem_dc, bitmap):
            # PW_CLIENTONLY (1) | PW_RENDERFULLCONTENT: client area only, so
            # the image matches the coordinate space actions use.
            if not user32.PrintWindow(
                ctypes.c_void_p(hwnd), mem_dc, 1 | _PW_RENDERFULLCONTENT
            ):
                return None
            img = _bitmap_to_image(mem_dc, bitmap, w, h)
    finally:
        user32.ReleaseDC(ctypes.c_void_p(hwnd), window_dc)
    # All-black usually means a GPU-composited window PrintWindow can't see.
    if img.getbbox() is None:
        return None
    return img


def capture_screen_region(x: int, y: int, w: int, h: int) -> "Image.Image":
    """Capture a screen rectangle via BitBlt from the desktop DC.

    Captures whatever is composited on screen (works for DirectX windows in
    the foreground); physical pixels, virtual-desktop coordinates.
    """
    user32, gdi32 = _user32(), _gdi32()
    screen_dc = user32.GetDC(None)
    if not screen_dc:
        raise QirabotError("GetDC(desktop) failed", code="windows.capture_failed")
    try:
        with _mem_bitmap(screen_dc, w, h) as (mem_dc, bitmap):
            if not gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, x, y, _SRCCOPY):
                raise QirabotError("BitBlt failed", code="windows.capture_failed")
            return _bitmap_to_image(mem_dc, bitmap, w, h)
    finally:
        user32.ReleaseDC(None, screen_dc)


# ---------------------------------------------------------------------------
# SendInput
# ---------------------------------------------------------------------------

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

# KEYBDINPUT flags
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# MOUSEINPUT flags
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

WHEEL_DELTA = 120

_ULONG_PTR = ctypes.c_size_t


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("union", _INPUTUNION)]

    # Introspection sugar so tests (and repr-debugging) read naturally.
    @property
    def mi(self) -> _MOUSEINPUT:
        mi: _MOUSEINPUT = self.union.mi
        return mi

    @property
    def ki(self) -> _KEYBDINPUT:
        ki: _KEYBDINPUT = self.union.ki
        return ki


def key_scancode_event(scancode: int, extended: bool, up: bool) -> INPUT:
    """A DirectInput-visible key event (games read these; virtual keys they don't)."""
    flags = KEYEVENTF_SCANCODE
    if extended:
        flags |= KEYEVENTF_EXTENDEDKEY
    if up:
        flags |= KEYEVENTF_KEYUP
    ev = INPUT(type=INPUT_KEYBOARD)
    ev.union.ki = _KEYBDINPUT(wVk=0, wScan=scancode, dwFlags=flags, time=0, dwExtraInfo=0)
    return ev


def key_unicode_events(char: str, up: bool) -> list[INPUT]:
    """KEYEVENTF_UNICODE event(s) for one character (surrogate pair aware)."""
    flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
    encoded = char.encode("utf-16-le")
    events = []
    for i in range(0, len(encoded), 2):
        unit = int.from_bytes(encoded[i : i + 2], "little")
        ev = INPUT(type=INPUT_KEYBOARD)
        ev.union.ki = _KEYBDINPUT(wVk=0, wScan=unit, dwFlags=flags, time=0, dwExtraInfo=0)
        events.append(ev)
    return events


def mouse_event(
    flags: int, dx: int = 0, dy: int = 0, data: int = 0
) -> INPUT:
    ev = INPUT(type=INPUT_MOUSE)
    ev.union.mi = _MOUSEINPUT(
        dx=dx, dy=dy, mouseData=ctypes.c_uint32(data & 0xFFFFFFFF).value,
        dwFlags=flags, time=0, dwExtraInfo=0,
    )
    return ev


def _last_error() -> int:
    getter = getattr(ctypes, "get_last_error", None)
    return int(getter()) if getter else 0


def send_inputs(events: list[INPUT]) -> None:
    """Inject events atomically via one SendInput call."""
    if not events:
        return
    array = (INPUT * len(events))(*events)
    sent = _user32().SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise QirabotError(
            f"SendInput injected {sent}/{len(events)} events "
            f"(err={_last_error()})",
            code="windows.sendinput_failed",
        )


# ---------------------------------------------------------------------------
# Foreground management
# ---------------------------------------------------------------------------

_SW_RESTORE = 9


def _same_hwnd(a: Any, b: Any) -> bool:
    """Compare HWNDs across ctypes representations.

    Window handles are 32-bit values that 64-bit Windows sign-extends, so the
    SAME handle surfaces as 0x80001234 (raw), -2147479020 (default c_int
    restype, e.g. GetForegroundWindow here) or 0xFFFFFFFF80001234
    (``int(c_void_p)``, e.g. the EnumWindows callback that resolves
    ``Window.hwnd``) depending on which API path produced it. Naive ``==``
    then reports a foreground window as "not foreground" forever — and
    ensure_foreground fires its ALT-tap unlock on every action, releasing any
    modifier key held around a click. Compare the low 32 bits.
    """
    return (int(a or 0) & 0xFFFFFFFF) == (int(b or 0) & 0xFFFFFFFF)


def is_foreground(hwnd: int) -> bool:
    """True if ``hwnd`` is the current foreground window (side-effect free)."""
    return _same_hwnd(_user32().GetForegroundWindow(), hwnd)


def ensure_foreground(hwnd: int) -> bool:
    """Bring ``hwnd`` to the foreground (best-effort; returns success).

    Games only receive DirectInput while foreground. SetForegroundWindow is
    refused unless our process owns the current foreground input; the standard
    unlock is injecting a no-op ALT tap first. A minimized window also needs
    SW_RESTORE before it can take focus.

    NOTE: the ALT-tap unlock injects an ALT press+release — callers holding a
    modifier around a click must repair it afterwards (see the Windows
    adapter's ``_reassert_held_keys``). Check :func:`is_foreground` first when
    input side effects would be unacceptable.
    """
    user32 = _user32()
    if is_foreground(hwnd):
        return True
    user32.ShowWindow(ctypes.c_void_p(hwnd), _SW_RESTORE)
    user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    if _same_hwnd(user32.GetForegroundWindow(), hwnd):
        return True
    # ALT tap unlocks SetForegroundWindow for this process, then retry.
    alt = 0x38  # LALT scancode
    send_inputs([key_scancode_event(alt, False, False), key_scancode_event(alt, False, True)])
    user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
    time.sleep(0.05)
    return bool(_same_hwnd(user32.GetForegroundWindow(), hwnd))


# ---------------------------------------------------------------------------
# IME
# ---------------------------------------------------------------------------

WM_INPUTLANGCHANGEREQUEST = 0x0050
_WM_IME_CONTROL = 0x0283
_IMC_GETCONVERSIONMODE = 0x0001
_IMC_SETCONVERSIONMODE = 0x0002
_IMC_GETOPENSTATUS = 0x0005
_IMC_SETOPENSTATUS = 0x0006
_IME_CMODE_NATIVE = 0x0001
_IME_CMODE_ALPHANUMERIC = 0x0000
_KLF_ACTIVATE = 0x0001
_EN_US_LAYOUT = "00000409"
_SMTO_ABORTIFHUNG = 0x0002
_SMTO_TIMEOUT_MS = 200


def _imm32() -> Any:
    return _dll("imm32")


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("hwndActive", ctypes.c_void_p),
        ("hwndFocus", ctypes.c_void_p),
        ("hwndCapture", ctypes.c_void_p),
        ("hwndMenuOwner", ctypes.c_void_p),
        ("hwndMoveSize", ctypes.c_void_p),
        ("hwndCaret", ctypes.c_void_p),
        ("rcCaret", _RECT),
    ]


def focused_control(hwnd: int) -> int:
    """The window with keyboard focus in ``hwnd``'s UI thread, or ``hwnd``.

    IME open/conversion state lives on the input context the FOCUSED window
    activates — an edit box focused after we touched the top-level window
    brings its own context (still open, still CJK) right back. IME messages
    must therefore target the focused child, not the top-level handle.
    """
    user32 = _user32()
    _set_types(
        user32.GetWindowThreadProcessId, ctypes.c_uint32, [ctypes.c_void_p, ctypes.c_void_p]
    )
    tid = user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), None)
    if not tid:
        return hwnd
    info = _GUITHREADINFO()
    info.cbSize = ctypes.sizeof(_GUITHREADINFO)
    if user32.GetGUIThreadInfo(ctypes.c_uint32(tid), ctypes.byref(info)) and info.hwndFocus:
        return int(info.hwndFocus)
    return hwnd


def _send_message_timeout(hwnd: Any, msg: int, wparam: int, lparam: Any) -> int | None:
    """SendMessageTimeoutW wrapper: the message result, or None if it failed.

    Plain SendMessageW blocks forever on a hung target — IME state is
    best-effort and must never block input, so every cross-process message
    here goes through the timeout variant.
    """
    user32 = _user32()
    _set_types(
        user32.SendMessageTimeoutW,
        ctypes.c_ssize_t,
        [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_size_t),
        ],
    )
    result = ctypes.c_size_t(0)
    ok = user32.SendMessageTimeoutW(
        ctypes.c_void_p(hwnd), msg, wparam, lparam,
        _SMTO_ABORTIFHUNG, _SMTO_TIMEOUT_MS, ctypes.byref(result),
    )
    return int(result.value) if ok else None


def ensure_english_input(hwnd: int) -> bool:
    """Force English input on ``hwnd``'s focused control; True when verified.

    An active CJK IME swallows injected scancode letters into its composition
    window instead of delivering them to the game. Three messages, all aimed
    at the focused control (see :func:`focused_control`): switch the thread's
    layout to US English, set the IME conversion mode to alphanumeric (the
    中/英 toggle of e.g. Microsoft Pinyin), and close the IME. The state is
    read back afterwards: a False return means the window kept its CJK IME
    (TSF-managed apps can ignore all of this) and the caller should deliver
    text through a path that bypasses composition, e.g. paste. Only the
    target window is touched (the user can switch back with Win+Space).
    """
    user32 = _user32()
    target = focused_control(hwnd)
    _set_types(
        user32.LoadKeyboardLayoutW, ctypes.c_void_p, [ctypes.c_wchar_p, ctypes.c_uint32]
    )
    hkl = user32.LoadKeyboardLayoutW(_EN_US_LAYOUT, _KLF_ACTIVATE)
    if hkl:
        _send_message_timeout(target, WM_INPUTLANGCHANGEREQUEST, 0, ctypes.c_void_p(hkl))
    imm32 = _imm32()
    _set_types(imm32.ImmGetDefaultIMEWnd, ctypes.c_void_p, [ctypes.c_void_p])
    ime = imm32.ImmGetDefaultIMEWnd(ctypes.c_void_p(target))
    if not ime:
        return True  # no IME attached to that thread: nothing swallows scancodes
    _send_message_timeout(ime, _WM_IME_CONTROL, _IMC_SETCONVERSIONMODE, _IME_CMODE_ALPHANUMERIC)
    _send_message_timeout(ime, _WM_IME_CONTROL, _IMC_SETOPENSTATUS, 0)
    # Read back: IME closed, or open but in alphanumeric (英) mode, both mean
    # scancode letters arrive untouched.
    open_status = _send_message_timeout(ime, _WM_IME_CONTROL, _IMC_GETOPENSTATUS, 0)
    if open_status is None:
        return False
    if not open_status:
        return True
    conv = _send_message_timeout(ime, _WM_IME_CONTROL, _IMC_GETCONVERSIONMODE, 0)
    return conv is not None and not (conv & _IME_CMODE_NATIVE)


# ---------------------------------------------------------------------------
# Clipboard (backs the adapter's paste path for text games can't receive via
# KEYEVENTF_UNICODE — see WindowsAdapter.type_focused)
# ---------------------------------------------------------------------------

CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002


def _set_types(fn: Any, restype: Any, argtypes: list[Any]) -> None:
    # HANDLEs/pointers are pointer-sized; ctypes' default c_int restype
    # truncates them on 64-bit. Test fakes (bound methods) reject attribute
    # assignment — same best-effort pattern as dpi_awareness.
    try:
        fn.restype = restype
        fn.argtypes = argtypes
    except Exception:
        pass


def _open_clipboard() -> None:
    """OpenClipboard, retrying briefly — another process may hold it."""
    user32 = _user32()
    for _ in range(10):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.02)
    raise QirabotError(
        f"OpenClipboard failed (err={_last_error()})",
        code="windows.clipboard_busy",
    )


def get_clipboard_text() -> str | None:
    """Current clipboard text, or None if empty, non-text, or unavailable."""
    try:
        _open_clipboard()
    except QirabotError:
        return None
    user32, kernel32 = _user32(), _kernel32()
    _set_types(user32.GetClipboardData, ctypes.c_void_p, [ctypes.c_uint32])
    _set_types(kernel32.GlobalLock, ctypes.c_void_p, [ctypes.c_void_p])
    _set_types(kernel32.GlobalUnlock, ctypes.c_int, [ctypes.c_void_p])
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
        if not ptr:
            return None
        try:
            return str(ctypes.wstring_at(ptr))
        finally:
            kernel32.GlobalUnlock(ctypes.c_void_p(handle))
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> None:
    """Replace the clipboard contents with ``text`` (CF_UNICODETEXT)."""
    user32, kernel32 = _user32(), _kernel32()
    _set_types(kernel32.GlobalAlloc, ctypes.c_void_p, [ctypes.c_uint32, ctypes.c_size_t])
    _set_types(kernel32.GlobalLock, ctypes.c_void_p, [ctypes.c_void_p])
    _set_types(kernel32.GlobalUnlock, ctypes.c_int, [ctypes.c_void_p])
    _set_types(kernel32.GlobalFree, ctypes.c_void_p, [ctypes.c_void_p])
    _set_types(user32.SetClipboardData, ctypes.c_void_p, [ctypes.c_uint32, ctypes.c_void_p])
    data = text.encode("utf-16-le") + b"\x00\x00"
    handle = kernel32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
    if not handle:
        raise QirabotError(
            f"GlobalAlloc failed (err={_last_error()})",
            code="windows.clipboard_failed",
        )
    ptr = kernel32.GlobalLock(ctypes.c_void_p(handle))
    if not ptr:
        kernel32.GlobalFree(ctypes.c_void_p(handle))
        raise QirabotError(
            f"GlobalLock failed (err={_last_error()})",
            code="windows.clipboard_failed",
        )
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(ctypes.c_void_p(handle))
    try:
        _open_clipboard()
    except QirabotError:
        kernel32.GlobalFree(ctypes.c_void_p(handle))
        raise
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, ctypes.c_void_p(handle)):
            # Ownership only transfers on success; free our copy on failure.
            kernel32.GlobalFree(ctypes.c_void_p(handle))
            raise QirabotError(
                f"SetClipboardData failed (err={_last_error()})",
                code="windows.clipboard_failed",
            )
    finally:
        user32.CloseClipboard()
