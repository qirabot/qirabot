"""Host display enumeration for the desktop backend's ``display=`` selection.

One numbering everywhere — ``Qirabot(display=N)``, ``QIRA_DISPLAY``, the CLI's
``--display N`` and ``qirabot displays`` — 1-based, and 1 is always the
PRIMARY display (macOS: the one with the menu bar; Windows: the primary
monitor). Display 1 is also what the desktop backend has always driven, so
omitting the option changes nothing.

Each :class:`Display` carries its rectangle in the coordinate space the
desktop backend's INPUT uses, so an adapter only has to add ``(x, y)`` to a
display-relative point:

* macOS — Quartz global coordinates in points (origin at the primary
  display's top-left, y down; a display left of/above the primary has a
  negative origin). Exactly what pyautogui's CGEvent input takes.
* Windows — physical pixels in virtual-desktop coordinates, enumerated under
  a per-monitor-DPI thread context. The adapter performs its input under the
  same context, so mixed-DPI setups line up (system-DPI-aware coordinates
  are virtualized on secondary monitors and would drift).

Linux only knows display 1 (the X screen pyautogui drives); asking for any
other number fails with an actionable error rather than silently driving the
wrong screen.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any

from qirabot.exceptions import QirabotError

#: Environment fallback for ``Qirabot(display=...)`` / ``--display``.
DISPLAY_ENV = "QIRA_DISPLAY"


@dataclass(frozen=True)
class Display:
    """One host display. ``x``/``y``/``width``/``height`` are in the desktop
    backend's input coordinate space (see the module docstring); ``scale``
    is captured-pixels per coordinate unit (2.0 on a Retina display, 1.0 on
    Windows where coordinates are already physical)."""

    index: int
    x: int
    y: int
    width: int
    height: int
    primary: bool
    name: str = ""
    # macOS CGDirectDisplayID (lets the overlay helper find the matching
    # NSScreen); 0 where the platform has no such id.
    id: int = 0
    scale: float = 1.0

    def describe(self) -> str:
        """One listing line, e.g. ``2   1710x1112  @ (-1710, 0)  Built-in``."""
        star = "*" if self.primary else " "
        tag = "  [primary]" if self.primary else ""
        return (
            f"{self.index}{star}  {self.width}x{self.height}"
            f"  @ ({self.x}, {self.y})  {self.name}{tag}"
        )


def parse_display(value: Any, source: str = "display") -> int | None:
    """Validate a display number from an argument or the environment.

    ``None``/``""`` mean "not set". Anything else must be an integer >= 1;
    a bad value raises ``ValueError`` naming ``source`` (an argument name or
    the env var), so typos fail loudly instead of driving display 1.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{source} must be a display number >= 1, got {value!r}")
    try:
        n = int(str(value).strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{source} must be a display number >= 1, got {value!r}") from None
    if n < 1 or (not isinstance(value, str) and n != value):
        raise ValueError(f"{source} must be a display number >= 1, got {value!r}")
    return n


def display_from_env() -> int | None:
    """``QIRA_DISPLAY`` parsed (``None`` when unset); raises on a bad value."""
    return parse_display(os.environ.get(DISPLAY_ENV, ""), DISPLAY_ENV)


def list_displays() -> list[Display]:
    """Every active display, primary first (index 1).

    Raises :class:`QirabotError` (``display.unsupported``) on platforms
    without enumeration, and ``MissingDependencyError`` when the macOS
    CoreGraphics bindings (part of the ``desktop`` extra) are missing.
    """
    if sys.platform == "darwin":
        return _list_macos()
    if sys.platform == "win32":
        return _list_windows()
    return _list_single()


def get_display(index: int) -> Display:
    """The display numbered ``index``; raises :class:`QirabotError`
    (``display.not_found``) listing the available ones when out of range."""
    displays = list_displays()
    for d in displays:
        if d.index == index:
            return d
    listing = "; ".join(d.describe() for d in displays) or "none detected"
    raise QirabotError(
        f"display {index} not found — available: {listing} "
        "(run `qirabot displays` to list them)",
        code="display.not_found",
    )


# ---------------------------------------------------------------------------
# macOS
# ---------------------------------------------------------------------------


def _list_macos() -> list[Display]:
    from qirabot._optional import require

    quartz: Any = require("Quartz", "desktop")
    err, ids, count = quartz.CGGetActiveDisplayList(32, None, None)
    if err:
        raise QirabotError(
            f"CGGetActiveDisplayList failed (error {err})", code="display.unsupported"
        )
    ids = list(ids[:count])
    main = quartz.CGMainDisplayID()
    # CGGetActiveDisplayList already lists the main display first; enforce
    # it anyway — index 1 == primary is the contract, and the order is also
    # what `screencapture -D` and avfoundation's "Capture screen K" follow.
    if main in ids:
        ids.remove(main)
        ids.insert(0, main)
    names = _macos_screen_names()
    out: list[Display] = []
    for i, did in enumerate(ids, start=1):
        b = quartz.CGDisplayBounds(did)
        w, h = int(b.size.width), int(b.size.height)
        try:
            mode = quartz.CGDisplayCopyDisplayMode(did)
            px = quartz.CGDisplayModeGetPixelWidth(mode) if mode else 0
            scale = px / w if px and w else 1.0
        except Exception:
            scale = 1.0
        name = names.get(int(did), "")
        if not name:
            builtin = bool(quartz.CGDisplayIsBuiltin(did))
            name = "Built-in Display" if builtin else f"Display {did}"
        out.append(
            Display(
                index=i,
                x=int(b.origin.x),
                y=int(b.origin.y),
                width=w,
                height=h,
                primary=did == main,
                name=name,
                id=int(did),
                scale=round(scale, 3),
            )
        )
    return out


def _macos_screen_names() -> dict[int, str]:
    """CGDirectDisplayID -> human name (NSScreen.localizedName, 10.15+).
    Best-effort: names are cosmetic, never worth failing over."""
    try:
        import AppKit

        names: dict[int, str] = {}
        for screen in AppKit.NSScreen.screens():
            num = screen.deviceDescription().get("NSScreenNumber")
            if num is not None:
                names[int(num)] = str(screen.localizedName())
        return names
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

_MONITORINFOF_PRIMARY = 0x1


def _list_windows() -> list[Display]:
    import ctypes
    from ctypes import wintypes

    from qirabot.windows import _user32, dpi_awareness

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    user32 = _user32()
    found: list[tuple[bool, int, int, int, int, str]] = []
    # getattr: typeshed only defines WINFUNCTYPE under win32, lint runs on
    # every platform (same trick as windows._dll).
    proc_type = getattr(ctypes, "WINFUNCTYPE")(
        wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(wintypes.RECT), wintypes.LPARAM,
    )

    def _cb(hmon: Any, _hdc: Any, _rect: Any, _lp: Any) -> bool:
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            found.append((
                bool(info.dwFlags & _MONITORINFOF_PRIMARY),
                r.left, r.top, r.right - r.left, r.bottom - r.top,
                info.szDevice,
            ))
        return True

    callback = proc_type(_cb)
    try:
        # Pointer-sized handles: ctypes' default int conversion truncates a
        # 64-bit HMONITOR (same pitfall as dpi_awareness' context handle).
        user32.GetMonitorInfoW.argtypes = [
            wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW),
        ]
        user32.EnumDisplayMonitors.argtypes = [
            wintypes.HDC, ctypes.c_void_p, proc_type, wintypes.LPARAM,
        ]
    except Exception:
        pass  # test fakes reject attribute assignment
    # Physical pixels: see the module docstring for why input runs in the
    # same per-monitor-DPI context.
    with dpi_awareness():
        user32.EnumDisplayMonitors(None, None, callback, 0)
    if not found:
        raise QirabotError("no monitors enumerated", code="display.unsupported")
    # Stable sort: primary first, the rest in enumeration order.
    found.sort(key=lambda m: not m[0])
    return [
        Display(
            index=i, x=x, y=y, width=w, height=h, primary=primary,
            name=str(device).replace("\\\\.\\", ""),
        )
        for i, (primary, x, y, w, h, device) in enumerate(found, start=1)
    ]


# ---------------------------------------------------------------------------
# Linux / other
# ---------------------------------------------------------------------------


def _list_single() -> list[Display]:
    """Linux and anything else: the one screen pyautogui drives."""
    from qirabot._optional import require

    pag: Any = require("pyautogui", "desktop")
    size = pag.size()
    return [
        Display(
            index=1, x=0, y=0, width=int(size.width), height=int(size.height),
            primary=True, name=os.environ.get("DISPLAY", "") or "screen",
        )
    ]


def supports_multi_display() -> bool:
    """True where display numbers other than 1 can be honored."""
    return sys.platform in ("darwin", "win32")
