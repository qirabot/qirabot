"""Overlay helper process: the on-screen progress window itself.

Runs as ``python -m qirabot._overlay_helper``, spawned by
:class:`qirabot.overlay.Overlay`. A separate process because macOS requires
the GUI to own the process main thread, which the user's script already
occupies — a thread cannot host AppKit, a child process can.

Protocol: one JSON object per stdin line; keys combine freely.
    {"title": "..."}                 the headline (the running instruction)
    {"state": "run" | "ok" | "fail"} status glyph; "run" (re)starts the
                                     elapsed timer, "ok"/"fail" freeze it
    {"text": "..."}                  the body (current step + reasoning)
    {"edge": true | false,           screen-edge "being controlled" glow:
     "hint": "Hold ESC…"}            a gradient band per screen border —
                                     amber at the edge fading inward —
                                     slow-breathing while the bot owns the
                                     real mouse/keyboard; "hint" (optional,
                                     with edge:true) is shown in a small
                                     top-center pill so the kill switch is
                                     discoverable
    {"cmd": "close", "linger": 1.5}  exit after `linger` seconds (default 0),
                                     so the final ✓/✗ state is readable
stdin EOF also exits, so a dying parent can never leave the window behind.

The reverse direction (helper → parent, one JSON line on stdout) carries
user-intent events: {"event": "abort"} when the user holds ESC for
~_ESC_HOLD_SECONDS while the edge glow is on — the parent ends the run
between steps. The hold threshold doubles as the injected-input filter: the
bot's own ESC presses are ~25ms taps and never reach it. Detection is
strictly OUT of the input delivery path — macOS uses a passive NSEvent
global monitor, Windows polls GetAsyncKeyState from the glow's animation
loop. Never a low-level keyboard hook: one lived here briefly and its
callback latency (sharing a thread with the glow animation) batched the
bot's injected keystrokes into single game frames, dropping characters.

Layout — title row (status glyph · title · elapsed clock) over a wrapping
body. The parent clips every field to a hard character budget before
sending; the labels additionally truncate visually, so oversized CJK text
(wider per char than the budget assumes) degrades to a clean cut, never a
broken layout. The elapsed clock ticks locally, off a 1s timer — it costs
no pipe traffic.

The window is excluded from screen capture (macOS: NSWindowSharingNone,
verified against the pyautogui/`screencapture` path; Windows:
WDA_EXCLUDEFROMCAPTURE) and is click-through and non-activating, so it
neither appears in bot screenshots nor interferes with input.

The edge strips inherit all of that, with one deliberate difference: on
Windows they REFUSE the WDA_MONITOR fallback the corner window uses. The
corner window degrades to a small black box in captures — tolerable; edge
strips would degrade to black bars framing every screenshot, blinding the
vision loop. No exclusion, no glow.

Exit codes: 0 normal, 3 unsupported platform or missing GUI dependency.
"""

from __future__ import annotations

import io
import json
import math
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

_WIDTH, _HEIGHT, _MARGIN = 340, 96, 24

# Edge glow: a soft GRADIENT band per screen edge — strongest at the border,
# fading to nothing inward (the screen-share-glow look) — breathing slowly,
# never flashing: it runs for the whole task and a blink rate would be
# hostile. The wide alpha swing is what makes it read from the corner of the
# eye (motion beats brightness); the gradient already fades to zero inward,
# so a full-strength ceiling costs no legibility. macOS draws a real
# NSGradient; tkinter has no per-pixel alpha, so Windows approximates with
# _EDGE_LAYERS nested strips on a falloff curve.
_EDGE_COLOR = "#f5c542"  # the same amber as the "run" state dot
_EDGE_ALPHA_LO, _EDGE_ALPHA_HI = 0.40, 1.0
_EDGE_PERIOD = 1.8  # seconds per breath
_EDGE_TICK_MS = 66  # ~15 fps; only ticks while the glow is on (Windows)
_EDGE_LAYERS = 5  # Windows gradient approximation: strips per edge

# Hold ESC this long (while the glow is on) to abort the run. Long enough
# that the bot's own injected ESC taps can never trip it, short enough to
# feel like a kill switch.
_ESC_HOLD_SECONDS = 1.0

# state -> (glyph, color as #rgb hex, also mapped to NSColor on macOS)
_STATES = {
    "run": ("●", "#f5c542"),   # amber: working
    "ok": ("✓", "#7ddf7d"),    # green: goal reached
    "fail": ("✗", "#f07171"),  # red: goal failed / errored
}


def _read_stdin(on_msg: Callable[[dict[str, Any]], object]) -> float:
    """Feed parsed stdin messages to ``on_msg``; return the close command's
    linger seconds (0.0 on EOF or a malformed value)."""
    # Explicit UTF-8: sys.stdin's default pipe encoding follows the locale
    # (GBK on Chinese Windows), which would garble any non-ASCII sender.
    # The parent sends ASCII-escaped JSON, so this is belt and braces.
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8", errors="replace")
    for raw in stdin:
        try:
            msg = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("cmd") == "close":
            try:
                return max(0.0, float(msg.get("linger", 0)))
            except (TypeError, ValueError):
                return 0.0
        on_msg(msg)
    return 0.0


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s >= 3600:
        return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"
    return f"{s // 60}:{s % 60:02d}"


def _win_dll(name: str) -> Any:
    """ctypes.WinDLL behind a platform guard — same trick as windows._dll:
    typeshed only defines WinDLL under win32, and lint runs on linux."""
    if sys.platform != "win32":
        raise OSError("win32 only")
    import ctypes

    return ctypes.WinDLL(name)


class _TimerState:
    """Elapsed-clock state shared by the message handler and the 1s tick.

    Both run on the GUI thread (performSelectorOnMainThread / Tk mainloop),
    so plain attributes suffice — no locking.
    """

    t0: float | None = None
    frozen: bool = False


class _EdgeState:
    """Edge-glow state; GUI-thread only, like :class:`_TimerState`.

    ``gen`` guards the Windows breathe loop against doubling: a fast
    off→on flip while the old after-callback is still pending would
    otherwise leave two loops running (double-rate breathing).
    """

    on: bool = False
    tick: int = 0
    gen: int = 0


def _edge_alpha(tick: int) -> float:
    """Breathing alpha for the ``tick``-th _EDGE_TICK_MS step: a sine ease
    between _EDGE_ALPHA_LO and _EDGE_ALPHA_HI with period _EDGE_PERIOD."""
    phase = (tick * _EDGE_TICK_MS / 1000.0) % _EDGE_PERIOD / _EDGE_PERIOD
    return _EDGE_ALPHA_LO + (_EDGE_ALPHA_HI - _EDGE_ALPHA_LO) * 0.5 * (
        1 - math.cos(2 * math.pi * phase)
    )


class _LongPress:
    """Held-key detector fed by down/up events plus periodic expiry polls.

    down() on key repeats does NOT reset the clock (the first press wins);
    expired() latches — it fires once per press, and the key must be
    released and pressed again to fire a second time. Injected key taps
    (the bot pressing ESC as a task action) are short, so the hold
    threshold filters them without inspecting the event source.
    """

    def __init__(self, threshold: float) -> None:
        self._threshold = threshold
        self._t0: float | None = None

    def down(self, now: float) -> None:
        if self._t0 is None:
            self._t0 = now

    def up(self) -> None:
        self._t0 = None

    def expired(self, now: float) -> bool:
        if self._t0 is not None and now - self._t0 >= self._threshold:
            self._t0 = None  # latch: one fire per press
            return True
        return False


def _emit_abort() -> None:
    """Tell the parent the user held ESC: one JSON line on stdout — the
    reverse direction of the stdin protocol. Best-effort; a closed pipe
    (parent already gone) must not kill the GUI."""
    try:
        sys.stdout.write(json.dumps({"event": "abort"}) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


def _run_macos() -> int:
    try:
        import AppKit
    except ImportError:
        return 3

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    screen = AppKit.NSScreen.mainScreen().frame()
    # NSWindow origin is bottom-left: this is the bottom-right corner.
    rect = AppKit.NSMakeRect(
        screen.size.width - _WIDTH - _MARGIN, _MARGIN, _WIDTH, _HEIGHT
    )
    style = (
        AppKit.NSWindowStyleMaskBorderless
        | AppKit.NSWindowStyleMaskNonactivatingPanel
    )
    panel = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        rect, style, AppKit.NSBackingStoreBuffered, False
    )
    panel.setSharingType_(AppKit.NSWindowSharingNone)
    panel.setOpaque_(False)
    panel.setBackgroundColor_(AppKit.NSColor.clearColor())
    panel.setLevel_(AppKit.NSStatusWindowLevel)
    panel.setIgnoresMouseEvents_(True)
    panel.setCollectionBehavior_(
        AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
        | AppKit.NSWindowCollectionBehaviorStationary
        | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
    )

    content = panel.contentView()
    content.setWantsLayer_(True)
    content.layer().setCornerRadius_(10.0)
    content.layer().setBackgroundColor_(
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.85).CGColor()
    )

    def _color(hex_rgb: str, alpha: float = 1.0) -> Any:
        r, g, b = (int(hex_rgb[i : i + 2], 16) / 255 for i in (1, 3, 5))
        return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, alpha)

    def _label(frame: Any, font: Any, color: Any = None) -> Any:
        field = AppKit.NSTextField.alloc().initWithFrame_(frame)
        field.setBezeled_(False)
        field.setDrawsBackground_(False)
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setFont_(font)
        field.setTextColor_(color or AppKit.NSColor.whiteColor())
        content.addSubview_(field)
        return field

    top_y, clock_w = _HEIGHT - 30, 52
    status = _label(
        AppKit.NSMakeRect(12, top_y, 16, 18), AppKit.NSFont.boldSystemFontOfSize_(12)
    )
    title = _label(
        AppKit.NSMakeRect(30, top_y, _WIDTH - 30 - clock_w - 12, 18),
        AppKit.NSFont.boldSystemFontOfSize_(12),
    )
    title.cell().setLineBreakMode_(AppKit.NSLineBreakByTruncatingTail)
    title.setStringValue_("qirabot")
    try:
        clock_font = AppKit.NSFont.monospacedDigitSystemFontOfSize_weight_(
            11, AppKit.NSFontWeightRegular
        )
    except AttributeError:
        clock_font = AppKit.NSFont.systemFontOfSize_(11)
    clock = _label(
        AppKit.NSMakeRect(_WIDTH - clock_w - 12, top_y, clock_w, 18),
        clock_font,
        AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.6, 1.0),
    )
    clock.setAlignment_(AppKit.NSTextAlignmentRight)
    body = _label(
        AppKit.NSMakeRect(12, 8, _WIDTH - 24, _HEIGHT - 42),
        AppKit.NSFont.systemFontOfSize_(12),
    )
    body.cell().setTruncatesLastVisibleLine_(True)
    panel.orderFrontRegardless()

    # Edge glow bands: same style/exclusion/click-through recipe as the
    # panel, one wide band per screen border, each holding a real gradient
    # (full amber at the screen border fading to clear inward — the
    # screen-share-glow look, not a solid bar). Each band is clipped to a
    # trapezoid with 45° mitered ends, so the four bands tile the frame
    # exactly like a picture frame — no corner overlap, no double-bright
    # corners. Ordered front once, visibility driven purely by window
    # alpha (0 = off) — no show/hide state to get wrong. Failure to build
    # them must never take down the progress window.
    edges: list[Any] = []
    try:
        span = 30.0  # points of gradient falloff — a tight glow, not a wash
        sw, sh = screen.size.width, screen.size.height

        def _glow_band(
            rect: Any, angle: float, outer_first: bool, clip: list[Any]
        ) -> Any:
            band = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                rect, style, AppKit.NSBackingStoreBuffered, False
            )
            band.setSharingType_(AppKit.NSWindowSharingNone)
            band.setOpaque_(False)
            band.setBackgroundColor_(AppKit.NSColor.clearColor())
            band.setLevel_(AppKit.NSStatusWindowLevel)
            band.setIgnoresMouseEvents_(True)
            band.setCollectionBehavior_(
                AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                | AppKit.NSWindowCollectionBehaviorStationary
                | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
            )
            w, h = rect.size.width, rect.size.height
            img = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(w, h))
            img.lockFocus()
            path = AppKit.NSBezierPath.bezierPath()
            path.moveToPoint_(clip[0])
            for pt in clip[1:]:
                path.lineToPoint_(pt)
            path.closePath()
            path.addClip()
            outer, inner = _color(_EDGE_COLOR, 1.0), _color(_EDGE_COLOR, 0.0)
            start, end = (outer, inner) if outer_first else (inner, outer)
            AppKit.NSGradient.alloc().initWithStartingColor_endingColor_(
                start, end
            ).drawInRect_angle_(AppKit.NSMakeRect(0, 0, w, h), angle)
            img.unlockFocus()
            view = AppKit.NSImageView.alloc().initWithFrame_(
                AppKit.NSMakeRect(0, 0, w, h)
            )
            view.setImage_(img)
            view.setImageScaling_(AppKit.NSImageScaleAxesIndependently)
            band.contentView().addSubview_(view)
            band.setAlphaValue_(0.0)
            band.orderFrontRegardless()
            return band

        # angle: NSGradient's start color sits at the angle's origin
        # (90° = drawn bottom-to-top, 0° = left-to-right). clip: the miter
        # trapezoid in band-local coords — outer edge full length, inner
        # edge inset by `span` at both ends (the 45° cut).
        P = AppKit.NSMakePoint
        edges = [
            _glow_band(  # top: outer edge at local y=span
                AppKit.NSMakeRect(0, sh - span, sw, span), 90.0, False,
                [P(0, span), P(sw, span), P(sw - span, 0), P(span, 0)],
            ),
            _glow_band(  # bottom: outer edge at local y=0
                AppKit.NSMakeRect(0, 0, sw, span), 90.0, True,
                [P(0, 0), P(sw, 0), P(sw - span, span), P(span, span)],
            ),
            _glow_band(  # left: outer edge at local x=0
                AppKit.NSMakeRect(0, 0, span, sh), 0.0, True,
                [P(0, 0), P(0, sh), P(span, sh - span), P(span, span)],
            ),
            _glow_band(  # right: outer edge at local x=span
                AppKit.NSMakeRect(sw - span, 0, span, sh), 0.0, False,
                [P(span, 0), P(span, sh), P(0, sh - span), P(0, span)],
            ),
        ]
    except Exception:
        edges = []

    # Kill-switch hint pill: top-center, shown only while the glow is on —
    # an invisible abort key is an abort key nobody uses. Same recipe as
    # everything else (capture-excluded, click-through, alpha-driven).
    hint_panel: Any = None
    hint_label: Any = None
    try:
        hint_w, hint_h = 340.0, 36.0
        scr_w = screen.size.width
        # Hang the pill below visibleFrame's top edge, not the screen's: the
        # full frame's top-center is exactly where the camera notch sits on
        # notched MacBooks (a hint hidden behind the notch is no hint), and
        # visibleFrame's top also clears the menu bar on every Mac.
        vf = AppKit.NSScreen.mainScreen().visibleFrame()
        pill_top = vf.origin.y + vf.size.height
        pill = AppKit.NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            AppKit.NSMakeRect(
                (scr_w - hint_w) / 2, pill_top - hint_h - 8, hint_w, hint_h
            ),
            style, AppKit.NSBackingStoreBuffered, False,
        )
        pill.setSharingType_(AppKit.NSWindowSharingNone)
        pill.setOpaque_(False)
        pill.setBackgroundColor_(AppKit.NSColor.clearColor())
        pill.setLevel_(AppKit.NSStatusWindowLevel)
        pill.setIgnoresMouseEvents_(True)
        pill.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary
            | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        pill_content = pill.contentView()
        pill_content.setWantsLayer_(True)
        pill_content.layer().setCornerRadius_(hint_h / 2)
        pill_content.layer().setBackgroundColor_(
            AppKit.NSColor.colorWithCalibratedWhite_alpha_(0.08, 0.92).CGColor()
        )
        # Amber ring, same color as the glow: ties the pill to the "being
        # controlled" state and separates it from any wallpaper — a dark
        # pill on a dark desktop was invisible.
        pill_content.layer().setBorderWidth_(1.5)
        pill_content.layer().setBorderColor_(_color(_EDGE_COLOR).CGColor())
        text = AppKit.NSTextField.alloc().initWithFrame_(
            AppKit.NSMakeRect(10, 8, hint_w - 20, hint_h - 16)
        )
        text.setBezeled_(False)
        text.setDrawsBackground_(False)
        text.setEditable_(False)
        text.setSelectable_(False)
        text.setFont_(AppKit.NSFont.boldSystemFontOfSize_(13))
        text.setTextColor_(AppKit.NSColor.whiteColor())
        text.setAlignment_(AppKit.NSTextAlignmentCenter)
        pill_content.addSubview_(text)
        pill.setAlphaValue_(0.0)
        pill.orderFrontRegardless()
        hint_panel, hint_label = pill, text
    except Exception:
        hint_panel = hint_label = None

    timer_state = _TimerState()
    edge_state = _EdgeState()

    # ESC-hold abort: a global key monitor feeds the detector; the breathe
    # timer polls expiry (it only matters while the glow is on, exactly when
    # that timer is doing work). Needs the Accessibility permission — the
    # same one pyautogui desktop control already requires — and silently
    # degrades to "no kill switch" without it.
    esc = _LongPress(_ESC_HOLD_SECONDS)
    try:
        _ESC_KEYCODE = 53

        def _on_key(event: Any) -> None:
            if event.keyCode() != _ESC_KEYCODE:
                return
            if event.type() == AppKit.NSEventTypeKeyDown:
                esc.down(time.monotonic())
            else:
                esc.up()

        AppKit.NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            AppKit.NSEventMaskKeyDown | AppKit.NSEventMaskKeyUp, _on_key
        )
    except Exception:
        pass

    # AppKit views may only be touched from the main thread, which app.run()
    # owns below — the stdin reader hands updates over with
    # performSelectorOnMainThread instead of calling the views directly.
    class _Bridge(AppKit.NSObject):
        def apply_(self, msg: Any) -> None:
            msg = dict(msg)
            if "title" in msg:
                title.setStringValue_(str(msg["title"]))
            state = msg.get("state")
            if state in _STATES:
                glyph, hex_rgb = _STATES[state]
                status.setStringValue_(glyph)
                status.setTextColor_(_color(hex_rgb))
                if state == "run":
                    timer_state.t0 = time.monotonic()
                    timer_state.frozen = False
                    clock.setStringValue_("0:00")
                else:
                    timer_state.frozen = True
            if "text" in msg:
                body.setStringValue_(str(msg["text"]))
            if "edge" in msg and edges:
                want = bool(msg["edge"])
                if want and msg.get("hint") and hint_label is not None:
                    hint_label.setStringValue_(str(msg["hint"]))
                if want != edge_state.on:
                    edge_state.on = want
                    edge_state.tick = 0
                    if hint_panel is not None:
                        hint_panel.setAlphaValue_(0.95 if want else 0.0)
                    if not want:
                        for strip in edges:
                            strip.setAlphaValue_(0.0)

        def tick_(self, _timer: Any) -> None:
            if timer_state.t0 is not None and not timer_state.frozen:
                clock.setStringValue_(
                    _fmt_elapsed(time.monotonic() - timer_state.t0)
                )

        def breathe_(self, _timer: Any) -> None:
            if not edge_state.on:
                return
            if esc.expired(time.monotonic()):
                _emit_abort()
            edge_state.tick += 1
            a = _edge_alpha(edge_state.tick)
            for strip in edges:
                strip.setAlphaValue_(a)

        def quit_(self, _sender: Any) -> None:
            app.terminate_(None)

    bridge = _Bridge.alloc().init()
    AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0, bridge, "tick:", None, True
    )
    if edges:
        # Repeating 15fps timer with an early return while off: cheaper to
        # reason about than start/stop churn, and macOS coalesces idle fires.
        AppKit.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            _EDGE_TICK_MS / 1000.0, bridge, "breathe:", None, True
        )

    def reader() -> None:
        linger = _read_stdin(
            lambda msg: bridge.performSelectorOnMainThread_withObject_waitUntilDone_(
                "apply:", msg, False
            )
        )
        if linger:
            time.sleep(linger)
        bridge.performSelectorOnMainThread_withObject_waitUntilDone_(
            "quit:", None, False
        )

    threading.Thread(target=reader, daemon=True).start()
    app.run()
    return 0


def _run_windows() -> int:
    import ctypes
    import queue

    try:
        import tkinter as tk
    except ImportError:
        # e.g. a Microsoft Store / embedded Python without tcl-tk.
        return 3

    # Per-monitor DPI awareness, before any window exists: without it the
    # process is scaled by the system and the bottom-right placement lands
    # away from the corner on >100% displays. Best-effort (needs Win 8.1+).
    try:
        _win_dll("shcore").SetProcessDpiAwareness(2)
    except Exception:
        pass

    BG = "#141414"
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-alpha", 0.88)  # also makes the window WS_EX_LAYERED,
    # which WDA_EXCLUDEFROMCAPTURE requires
    root.configure(bg=BG)

    # The process is per-monitor DPI aware (above), so window geometry is in
    # physical pixels — but Tk still sizes point-based fonts by the real DPI.
    # At 150% scaling the text renders 1.5x taller while a fixed 340x96 box
    # does not, and the body lines fall off the bottom edge. Scale every
    # hand-tuned pixel dimension by the same pixels-per-inch factor Tk uses
    # for the fonts, so text and box grow together.
    try:
        scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
    except Exception:
        scale = 1.0

    def px(v: int) -> int:
        return round(v * scale)

    top = tk.Frame(root, bg=BG)
    top.pack(fill="x", padx=px(12), pady=(px(8), 0))
    status = tk.Label(top, text="", bg=BG, fg="white", font=("Segoe UI", 10, "bold"))
    status.pack(side="left")
    clock = tk.Label(top, text="", bg=BG, fg="#999999", font=("Consolas", 9))
    clock.pack(side="right")
    # anchor+no wrap: overlong titles clip at the clock's edge instead of
    # pushing it out of the window.
    title = tk.Label(
        top, text="qirabot", bg=BG, fg="white",
        font=("Segoe UI", 10, "bold"), anchor="w",
    )
    title.pack(side="left", fill="x", expand=True, padx=(px(6), px(6)))
    body = tk.Label(
        root, text="", bg=BG, fg="white", font=("Segoe UI", 10),
        justify="left", anchor="nw", wraplength=px(_WIDTH - 24),
    )
    body.pack(fill="both", expand=True, padx=px(12), pady=(px(2), px(8)))
    # Extra bottom margin keeps the window clear of the taskbar.
    x = root.winfo_screenwidth() - px(_WIDTH) - px(_MARGIN)
    y = root.winfo_screenheight() - px(_HEIGHT) - px(_MARGIN) - px(48)
    root.geometry(f"{px(_WIDTH)}x{px(_HEIGHT)}+{x}+{y}")
    root.update_idletasks()

    user32 = _win_dll("user32")
    HWND = ctypes.c_void_p  # pointer-sized, so 64-bit handles don't truncate
    user32.GetAncestor.argtypes = [HWND, ctypes.c_uint]
    user32.GetAncestor.restype = HWND
    user32.SetWindowDisplayAffinity.argtypes = [HWND, ctypes.c_uint]
    # 32-bit user32 doesn't export the *Ptr variants (they're macros there);
    # ex-style values fit in 32 bits either way.
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_long.argtypes = [HWND, ctypes.c_int]
    set_long.argtypes = [HWND, ctypes.c_int, ctypes.c_long]
    user32.SetWindowPos.argtypes = [
        HWND, HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.c_uint,
    ]

    WDA_EXCLUDEFROMCAPTURE = 0x11  # Win10 2004+
    WDA_MONITOR = 0x01  # older fallback: black box in captures, never content
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020  # clicks pass through to what's below
    WS_EX_TOOLWINDOW = 0x00000080  # no taskbar / Alt-Tab entry
    WS_EX_LAYERED = 0x00080000
    WS_EX_NOACTIVATE = 0x08000000  # never steals focus from the target app
    HWND_TOPMOST = HWND(-1)
    SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
    SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x0010, 0x0020

    def _shield(widget: "tk.Misc", require_exclude: bool) -> bool:
        """Capture-exclude + click-through + no-activate ``widget``'s window.

        winfo_id() is a Tk child window; the display affinity and the
        click-through styles must go on the top-level ancestor.

        ``require_exclude`` refuses the WDA_MONITOR fallback: the corner
        window degrades to a small black box in captures (tolerable), but a
        caller building edge strips must get False back and not show them
        at all — black bars framing every capture would blind the bot.
        """
        hwnd = user32.GetAncestor(widget.winfo_id(), 2)  # GA_ROOT
        if not user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            if require_exclude:
                return False
            user32.SetWindowDisplayAffinity(hwnd, WDA_MONITOR)
        ex_style = get_long(hwnd, GWL_EXSTYLE)
        set_long(
            hwnd,
            GWL_EXSTYLE,
            ex_style | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_LAYERED
            | WS_EX_NOACTIVATE,
        )
        # SetWindowLong alone doesn't reliably apply style changes to a
        # window that is already visible; SWP_FRAMECHANGED forces the
        # recalculation.
        user32.SetWindowPos(
            hwnd, HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        return True

    _shield(root, require_exclude=False)

    # Edge glow bands: tkinter has no per-pixel alpha, so the mac path's
    # real gradient is approximated with _EDGE_LAYERS nested strips per
    # edge on a falloff curve — full amber at the border stepping down to
    # near-clear inward. Like the mac path: always mapped, visibility
    # driven purely by alpha (which also makes them WS_EX_LAYERED, as the
    # display affinity requires). Any failure — including a pre-2004
    # Windows refusing WDA_EXCLUDEFROMCAPTURE — drops the strips, never
    # the window.
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    span = max(20, sh // 36)  # ~30px at 1080p, scales with physical pixels
    edges: list[tuple[tk.Toplevel, float]] = []  # (strip, falloff 0..1)
    try:
        lt = max(2, span // _EDGE_LAYERS)  # per-layer thickness
        for i in range(_EDGE_LAYERS):
            falloff = (1 - i / _EDGE_LAYERS) ** 1.5
            off = i * lt
            # Each layer is inset by its own depth at both ends — a stepped
            # 45° miter, so bands don't stack alpha in the corner squares
            # (residual overlap shrinks to lt×lt per layer, invisible).
            for w, h, x0, y0 in (
                (sw - 2 * off, lt, off, off),            # top, stepping inward
                (sw - 2 * off, lt, off, sh - lt - off),  # bottom
                (lt, sh - 2 * off, off, off),            # left
                (lt, sh - 2 * off, sw - lt - off, off),  # right
            ):
                strip = tk.Toplevel(root)
                strip.overrideredirect(True)
                strip.attributes("-topmost", True)
                strip.attributes("-alpha", 0.0)
                strip.configure(bg=_EDGE_COLOR)
                strip.geometry(f"{w}x{h}+{x0}+{y0}")
                edges.append((strip, falloff))
        root.update_idletasks()
        for strip, _falloff in edges:
            if not _shield(strip, require_exclude=True):
                raise OSError("capture exclusion unavailable for edge strips")
    except Exception:
        for strip, _falloff in edges:
            try:
                strip.destroy()
            except Exception:
                pass
        edges = []

    # Kill-switch hint pill: top-center, shown only while the glow is on.
    # Edge-strip exclusion policy (no capture exclusion → no pill): a hint
    # in every screenshot would confuse the model.
    hint_win: "tk.Toplevel | None" = None
    hint_label: "tk.Label | None" = None
    try:
        pill = tk.Toplevel(root)
        pill.overrideredirect(True)
        pill.attributes("-topmost", True)
        pill.attributes("-alpha", 0.0)
        # Amber toplevel showing through as a 2px ring around the dark
        # label: ties the pill to the glow and separates it from any
        # wallpaper — a dark pill on a dark desktop was invisible.
        pill.configure(bg=_EDGE_COLOR)
        pill_label = tk.Label(
            pill, text="", bg=BG, fg="white",
            font=("Segoe UI", 11, "bold"), padx=px(14), pady=px(6),
        )
        pill_label.pack(padx=px(2), pady=px(2))
        pill.update_idletasks()
        pill.geometry(f"+{(sw - pill.winfo_reqwidth()) // 2}+{px(10)}")
        if _shield(pill, require_exclude=True):
            hint_win, hint_label = pill, pill_label
        else:
            pill.destroy()
    except Exception:
        hint_win = hint_label = None

    timer_state = _TimerState()
    edge_state = _EdgeState()

    # ESC-hold abort, by POLLING GetAsyncKeyState from the breathe loop —
    # deliberately NOT a WH_KEYBOARD_LL hook. A low-level hook serializes
    # EVERY keyboard event system-wide (including the bot's own SendInput
    # stream) through this thread, which is busy animating ~21 layered
    # windows while the glow is on; the induced stalls batched injected
    # keystrokes into single game frames, and frame-polling games dropped
    # characters (the v2.4.1 "trackquestinfo on" → "trackquestio"
    # regression). Polling never touches the input delivery path, and the
    # breathe cadence (~15 Hz) is ample for a 1s hold. Trade-off: polling
    # can't tell injected ESC from a physical one — the hold threshold does
    # that instead (the bot's taps are ~25ms); only a scripted
    # press_key("esc", duration_seconds>1) could false-trigger.
    esc = _LongPress(_ESC_HOLD_SECONDS)
    _VK_ESCAPE = 0x1B

    def _poll_esc() -> None:
        try:
            down = bool(user32.GetAsyncKeyState(_VK_ESCAPE) & 0x8000)
        except Exception:
            return
        now = time.monotonic()
        if down:
            esc.down(now)
        else:
            esc.up()
        if esc.expired(now):
            _emit_abort()

    import tkinter.font as tkfont

    title_font = tkfont.Font(family="Segoe UI", size=10, weight="bold")

    def _fit_title(text: str) -> str:
        # tk labels clip at the pixel edge with no truncation cue, so a long
        # instruction just looks complete; trim to the label's real width and
        # add the ellipsis ourselves (macOS gets this for free from
        # NSLineBreakByTruncatingTail). Pixel-based, so CJK/latin mixes trim
        # correctly where a character-count budget could not.
        width = title.winfo_width()
        if width <= 1:  # first message can arrive before layout has run
            width = px(_WIDTH - 110)  # status + clock + paddings
        if title_font.measure(text) <= width:
            return text
        while text and title_font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def apply(msg: dict[str, Any]) -> None:
        if "title" in msg:
            title.config(text=_fit_title(str(msg["title"])))
        state = msg.get("state")
        if state in _STATES:
            glyph, color = _STATES[state]
            status.config(text=glyph, fg=color)
            if state == "run":
                timer_state.t0 = time.monotonic()
                timer_state.frozen = False
                clock.config(text="0:00")
            else:
                timer_state.frozen = True
        if "text" in msg:
            body.config(text=str(msg["text"]))
        if "edge" in msg and edges:
            want = bool(msg["edge"])
            if want and msg.get("hint") and hint_win is not None and hint_label is not None:
                hint_label.config(text=str(msg["hint"]))
                hint_win.update_idletasks()  # re-center: the text sets the width
                hint_win.geometry(f"+{(sw - hint_win.winfo_reqwidth()) // 2}+10")
            if want and not edge_state.on:
                edge_state.on = True
                edge_state.tick = 0
                edge_state.gen += 1
                if hint_win is not None:
                    hint_win.attributes("-alpha", 0.92)
                root.after(_EDGE_TICK_MS, breathe, edge_state.gen)
            elif not want and edge_state.on:
                edge_state.on = False
                if hint_win is not None:
                    hint_win.attributes("-alpha", 0.0)
                for strip, _falloff in edges:
                    strip.attributes("-alpha", 0.0)

    def breathe(gen: int) -> None:
        # gen: a stale loop from before an off→on flip must die here, or two
        # loops run the animation at double rate.
        if not edge_state.on or gen != edge_state.gen:
            return
        _poll_esc()
        edge_state.tick += 1
        a = _edge_alpha(edge_state.tick)
        for strip, falloff in edges:
            strip.attributes("-alpha", a * falloff)
        root.after(_EDGE_TICK_MS, breathe, gen)

    def tick() -> None:
        if timer_state.t0 is not None and not timer_state.frozen:
            clock.config(text=_fmt_elapsed(time.monotonic() - timer_state.t0))
        root.after(1000, tick)

    q: queue.Queue[tuple[str, Any]] = queue.Queue()
    _CLOSE = "close"

    def reader() -> None:
        linger = _read_stdin(lambda msg: q.put(("msg", msg)))
        q.put((_CLOSE, linger))

    def poll() -> None:
        try:
            while True:
                kind, value = q.get_nowait()
                if kind is _CLOSE:
                    root.after(int(value * 1000), root.destroy)
                    return
                apply(value)
        except queue.Empty:
            pass
        root.after(100, poll)

    threading.Thread(target=reader, daemon=True).start()
    root.after(100, poll)
    root.after(1000, tick)
    root.mainloop()
    return 0


def main() -> int:
    if sys.platform == "darwin":
        return _run_macos()
    if sys.platform == "win32":
        return _run_windows()
    return 3


if __name__ == "__main__":
    sys.exit(main())
