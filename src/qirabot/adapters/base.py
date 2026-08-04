"""Base adapter interface and device info."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("qirabot")


def split_combo(key: str) -> tuple[list[str], str]:
    """Split a key combo like ``"ctrl+shift+a"`` into modifiers and the final key.

    Returns ``(["ctrl", "shift"], "a")``; a single key yields ``([], "Enter")``.
    The ``+`` join is the wire convention the engine uses for press_key combos.
    Each adapter maps the returned names to its own framework vocabulary.
    """
    parts = [p.strip() for p in key.split("+")]
    return parts[:-1], parts[-1]


@dataclass
class ScreenshotConfig:
    """Screenshot format and quality settings."""

    format: str = "jpeg"
    quality: int = 80
    annotate: bool = False

    # Only jpeg/png are safe across every adapter: selenium/appium encode
    # anything non-png as JPEG, so an unvalidated value (e.g. "webp") would
    # mismatch the extension/mime_type derived below. Validate once here.
    _SUPPORTED_FORMATS = ("jpeg", "png")

    def __post_init__(self) -> None:
        fmt = self.format.lower()
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in self._SUPPORTED_FORMATS:
            raise ValueError(
                f"unsupported screenshot_format {self.format!r}; "
                f"expected one of: {', '.join(self._SUPPORTED_FORMATS)}"
            )
        self.format = fmt

    @property
    def mime_type(self) -> str:
        return f"image/{self.format}"

    @property
    def extension(self) -> str:
        return "jpg" if self.format == "jpeg" else self.format


@dataclass
class DeviceInfo:
    """Device metadata sent with each AI request.

    Deliberately minimal: only what the server consumes (platform) plus the
    screen dimensions. We do not collect host/OS fingerprinting metadata
    (hostname, os, arch, …) — it has no server-side use and the client is
    open-source, so it must not silently gather machine identifiers.
    """

    platform: str
    width: int
    height: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "width": self.width,
            "height": self.height,
        }


class DeviceAdapter(ABC):
    """Abstract adapter for any automation framework."""

    # Seconds to let the UI settle (animations, navigation, scroll inertia, app
    # launches) after a screen-changing action before the next screenshot. Most
    # frameworks fire input events and return immediately with no "wait until
    # stable" primitive, so a fixed delay is the pragmatic floor -- without it the
    # next shot can catch a pre-repaint / mid-animation frame and the model wrongly
    # concludes the action did nothing. Subclasses override with a per-platform
    # default (0.0 = no settle, e.g. Playwright, which auto-waits on its own).
    _SETTLE_SECONDS: float = 0.0

    # Actions that don't change the screen (or handle their own timing), so the
    # next screenshot needs no settle delay after them. hover is deliberately
    # NOT here: its whole purpose is to reveal delayed UI (tooltips/submenus),
    # so it needs the settle more than most actions, not less.
    _NO_SETTLE: frozenset[str] = frozenset({"wait", "done", "save_note"})

    # True when the adapter drives the machine's REAL input devices (global
    # mouse/keyboard) — the user must keep their hands off while a task runs.
    # The client uses this to light the screen-edge "being controlled" glow;
    # remote-protocol backends (browser/adb/wda) leave it False.
    controls_user_input: bool = False

    # Per-instance override of ``_SETTLE_SECONDS``; set by the client when the user
    # passes ``settle_seconds``. ``None`` falls back to the class default.
    _settle_override: float | None = None

    @property
    def settle_seconds(self) -> float:
        """Effective settle delay: user override if set, else the class default."""
        if self._settle_override is not None:
            return self._settle_override
        return self._SETTLE_SECONDS

    @abstractmethod
    def __init__(self, target: Any) -> None:
        """Wrap a framework target (page, driver, or module)."""
        ...

    @classmethod
    def accepts(cls, target: Any) -> bool:
        """Return True if this adapter can wrap ``target``.

        Consulted by :func:`qirabot.adapters.auto.detect` for auto-detection.
        Third-party adapters only need this when registered via
        ``register_adapter()``; adapters passed as instances to ``bind()``
        never go through detection, so the default of ``False`` is fine.
        """
        return False

    @abstractmethod
    def screenshot(self, config: ScreenshotConfig | None = None) -> bytes:
        ...

    @staticmethod
    def _encode_image(img: Any, cfg: ScreenshotConfig) -> bytes:
        """Encode a decoded PIL image in the configured screenshot format.

        JPEG has no alpha channel, so RGBA/paletted frames are flattened to
        RGB first. The one shared implementation of a conversion every
        adapter needs — keep format quirks here, not in subclasses.
        """
        import io

        buf = io.BytesIO()
        if cfg.format == "jpeg":
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(buf, format="JPEG", quality=cfg.quality)
        else:
            img.save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _reencode_png(png_bytes: bytes, cfg: ScreenshotConfig) -> bytes:
        """PNG source bytes -> bytes in the configured format.

        For adapters whose framework hands back PNG: PNG config passes the
        bytes through undecoded; JPEG decodes once and re-encodes.
        """
        if cfg.format == "png":
            return png_bytes
        import io

        from PIL import Image

        return DeviceAdapter._encode_image(Image.open(io.BytesIO(png_bytes)), cfg)

    @abstractmethod
    def click(self, x: float, y: float) -> None:
        ...

    @abstractmethod
    def double_click(self, x: float, y: float) -> None:
        ...

    def right_click(self, x: float, y: float) -> None:
        # Touch platforms have no secondary button; the plain-click fallback
        # is intentional but must not be invisible when debugging.
        logger.debug("%s: right_click degrades to click", type(self).__name__)
        self.click(x, y)

    def hover(self, x: float, y: float) -> None:
        logger.debug("%s: hover not supported; no-op", type(self).__name__)

    @abstractmethod
    def type_text(self, x: float, y: float, text: str) -> None:
        ...

    def type_focused(self, text: str) -> None:
        """Type into whatever currently has keyboard focus (no locating click)."""
        raise NotImplementedError(f"{type(self).__name__} does not support type_focused")

    def clear_text(self, x: float, y: float) -> None:
        self.click(x, y)
        self.clear_focused()

    def clear_focused(self) -> None:
        """Clear the currently focused field (no locating click)."""
        self.press_key("ctrl+a")
        self.press_key("Backspace")

    @abstractmethod
    def press_key(self, key: str) -> None:
        ...

    def _press_key_held(self, key: str, duration: float) -> None:
        """Hold ``key`` (or a ``+`` combo) for ``duration`` seconds, then release.

        Backends without the split key primitives (web/touch) degrade to an
        instant tap. That fallback relies on an invariant: ``key_down`` must
        raise ``NotImplementedError`` BEFORE any side effect, so it can never
        fire with keys half-pressed. Keep that invariant when implementing
        ``key_down`` in new adapters.
        """
        import time

        mods, base = split_combo(key)
        pressed: list[str] = []
        try:
            for k in mods + [base]:
                self.key_down(k)  # registers in _held_keys on desktop adapters
                pressed.append(k)
            time.sleep(duration)
        except NotImplementedError:
            self.press_key(key)  # web/touch: degrade to an instant tap
            return
        finally:
            release_failed = False
            for k in reversed(pressed):
                try:
                    self.key_up(k)
                except Exception:
                    release_failed = True
            if release_failed:
                # Direct SDK calls have no ai()-end sweep, so sweep here
                # rather than leave a key stuck until the next run.
                self.release_all_inputs()

    # How long modifier key(s) stay held before the click goes down (lead) and
    # after the button comes back up (tail). 50ms suits ordinary desktop apps;
    # games need far more lead — many animate into their modifier "mode"
    # (cursor unlock, overlay) over several frames and process a click that
    # arrives mid-transition as unmodified. Tunable per run via the
    # QIRA_MODIFIER_LEAD / QIRA_MODIFIER_TAIL env vars (seconds).
    _MODIFIER_LEAD: float = 0.05
    _MODIFIER_TAIL: float = 0.05

    @staticmethod
    def _env_seconds(name: str, default: float) -> float:
        import os

        raw = os.environ.get(name)
        if raw:
            try:
                return float(raw)
            except ValueError:
                pass
        return default

    def _click_with_modifiers(self, x: float, y: float, modifier: str) -> None:
        """Hold modifier key(s) (``+``-joined) around a click, then release.

        Backends without the split key primitives (web/touch) degrade to a
        plain click. Same invariant as ``_press_key_held``: ``key_down`` must
        raise ``NotImplementedError`` BEFORE any side effect.
        """
        import time

        keys = [p.strip() for p in modifier.split("+") if p.strip()]
        pressed: list[str] = []
        try:
            for k in keys:
                self.key_down(k)  # registers in _held_keys on desktop adapters
                pressed.append(k)
            # The modifier must be sampled as held BEFORE the click lands and
            # THROUGH the button release — and apps that transition into a
            # modifier mode need the whole lead to finish that transition.
            time.sleep(self._env_seconds("QIRA_MODIFIER_LEAD", self._MODIFIER_LEAD))
            self.click(x, y)
            time.sleep(self._env_seconds("QIRA_MODIFIER_TAIL", self._MODIFIER_TAIL))
        except NotImplementedError:
            self.click(x, y)  # web/touch: degrade to a plain click
            return
        finally:
            release_failed = False
            for k in reversed(pressed):
                try:
                    self.key_up(k)
                except Exception:
                    release_failed = True
            if release_failed:
                # Direct SDK calls have no ai()-end sweep, so sweep here
                # rather than leave a modifier stuck until the next run.
                self.release_all_inputs()

    @abstractmethod
    def scroll(self, x: float, y: float, direction: str, distance: int) -> None:
        ...

    def _scroll_action(self, action_type: str, params: dict[str, Any]) -> None:
        """One shared parse for the wire scroll actions (``scroll`` /
        ``scroll_at``).

        The engine sends ``{direction, amount}`` — ``amount`` in real pixels,
        no x/y for plain scroll, the resolved element's x/y for scroll_at.
        Direct callers may pass the legacy ``distance`` (~100px units)
        instead. The pixel amount is honored exactly; how it becomes a
        gesture is the adapter's :meth:`_scroll_pixels`. This used to live as
        a per-adapter ``_dispatch`` override, giving the same wire params two
        parse paths — keep it here only.
        """
        raw_amount = params.get("amount")
        if raw_amount is not None and raw_amount != "":
            pixels = int(raw_amount)
        elif params.get("distance") is not None:
            pixels = int(params["distance"]) * 100
        else:
            pixels = 0  # adapter-default nudge
        x = float(params.get("x") or 0)
        y = float(params.get("y") or 0)
        self._scroll_pixels(x, y, str(params.get("direction", "down")), pixels)

    def _scroll_pixels(self, x: float, y: float, direction: str, pixels: int) -> None:
        """Scroll ~``pixels`` px at (x, y). A 0/0 anchor means "adapter
        default" (typically screen center); ``pixels <= 0`` means the
        adapter's default nudge. The base maps back onto the legacy
        :meth:`scroll` unit (~100px per step); pixel-native backends (touch
        swipe, wheel notches) override.
        """
        distance = max(1, round(pixels / 100)) if pixels > 0 else 3
        self.scroll(x, y, direction, distance)

    def _swipe_scroll(
        self, cx: float, cy: float, direction: str, pixels: int, info: "DeviceInfo"
    ) -> None:
        """Touch-scroll geometry shared by the swipe backends (adb/WDA/Appium).

        Content moves ``direction``, so the finger swipes the opposite way
        from (cx, cy). Defaults to 60% of the span, capped at 70% so the
        whole gesture stays on-screen, endpoints clamped to the middle 90%.
        Ends in :meth:`_swipe_from_to` — the only per-backend part.
        (Geometry mirrors the retired airtest adapter's numbers.)
        """
        w, h = info.width, info.height
        span = h if direction in ("up", "down") else w
        if pixels <= 0:
            pixels = int(span * 0.6)
        pixels = min(pixels, int(span * 0.7))

        if direction == "down":
            ex, ey = cx, cy - pixels
        elif direction == "up":
            ex, ey = cx, cy + pixels
        elif direction == "right":
            ex, ey = cx - pixels, cy
        elif direction == "left":
            ex, ey = cx + pixels, cy
        else:
            return

        def clamp(v: float, lo: float, hi: float) -> float:
            return max(lo, min(hi, v))

        self._swipe_from_to(
            cx, cy, clamp(ex, w * 0.05, w * 0.95), clamp(ey, h * 0.05, h * 0.95)
        )

    def _swipe_from_to(
        self, from_x: float, from_y: float, to_x: float, to_y: float
    ) -> None:
        """One finger swipe primitive backing :meth:`_swipe_scroll`."""
        raise NotImplementedError(f"{type(self).__name__} does not support _swipe_from_to")

    def drag(self, from_x: float, from_y: float, to_x: float, to_y: float) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support drag")

    def long_press(self, x: float, y: float, duration: float = 2.0) -> None:
        """Press and hold at (x, y) for ``duration`` seconds (touch-only gesture)."""
        raise NotImplementedError(f"{type(self).__name__} does not support long_press")

    def mouse_down(self, x: float, y: float) -> None:
        """Press and HOLD the (left) mouse button at (x, y) without releasing.

        Pairs with :meth:`mouse_up`. Desktop-only primitive; the holder is
        responsible for the matching release (the client auto-releases any
        still-held input at the end of an ``ai()`` run and on ``close()``).
        """
        raise NotImplementedError(f"{type(self).__name__} does not support mouse_down")

    def mouse_up(self, x: float | None = None, y: float | None = None) -> None:
        """Release the (left) mouse button.

        With ``x``/``y`` the cursor moves there first (drag-to-target release);
        without them it releases at the current cursor position.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support mouse_up")

    def key_down(self, key: str) -> None:
        """Press and HOLD ``key`` without releasing (pairs with :meth:`key_up`)."""
        raise NotImplementedError(f"{type(self).__name__} does not support key_down")

    def key_up(self, key: str) -> None:
        """Release a key previously held with :meth:`key_down`."""
        raise NotImplementedError(f"{type(self).__name__} does not support key_up")

    def release_all_inputs(self) -> None:
        """Release every mouse button / key still held by this adapter.

        Safety net for the split press/release primitives: the client calls this
        at the end of an ``ai()`` run and on ``close()`` so a forgotten
        ``mouse_up``/``key_up`` can't leave an input stuck and silently corrupt
        every later action. No-op for adapters that don't hold state.
        """

    def navigate(self, url: str) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support navigate")

    def go_back(self) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support go_back")

    def close_tab(self) -> None:
        raise NotImplementedError(f"{type(self).__name__} does not support close_tab")

    @property
    def current_target(self) -> Any:
        """Return the current underlying target (may change after new-tab switches).

        Action methods return this via ``Qirabot._result()``, so it must not
        raise. The default of ``self`` keeps the adapter-cache round trip
        consistent for third-party adapters passed straight to ``bind()``;
        adapters wrapping a framework object should return that object instead.
        """
        return self

    @abstractmethod
    def device_info(self) -> DeviceInfo:
        ...

    def annotation_scale(self) -> float:
        """Screenshot pixels per coordinate-space logical unit.

        Crosshair annotation in the report is drawn on the raw screenshot, so
        coords from the model (in whatever space ``device_info`` advertises)
        must be multiplied by this factor to land at the visual click position.
        1.0 when the screenshot and coordinate space are the same — true for
        every adapter except Appium iOS, where ``get_window_size()`` reports
        logical points (e.g. 393x852) but ``get_screenshot_as_base64()`` returns
        physical pixels (e.g. 1179x2556 on a Retina display).
        """
        return 1.0

    def window_info(self) -> dict[str, Any] | None:
        """Identify the window under test, for per-window screen recording.

        Returns ``{"title": str | None, "hwnd": int | None}`` for backends bound
        to a concrete OS window (currently the Windows window backend), or ``None`` when
        there is no single window to follow (browsers, touch devices, whole-
        desktop automation). The recorder degrades to full-screen on ``None``.
        """
        return None

    def close(self) -> None:
        """Release any resources/listeners the adapter registered.

        No-op by default; adapters that hook into their framework (e.g. the
        Playwright context's ``page`` event) override this to unhook. Called by
        ``Qirabot.close()``.
        """

    def execute(self, action_type: str, params: dict[str, Any]) -> None:
        """Dispatch an action by type, then let the UI settle.

        After a screen-changing action (anything not in ``_NO_SETTLE``) we sleep
        ``settle_seconds`` so the next screenshot lands on the repainted frame.
        """
        self._dispatch(action_type, params)
        if action_type not in self._NO_SETTLE and self.settle_seconds:
            import time

            time.sleep(self.settle_seconds)

    def _dispatch(self, action_type: str, params: dict[str, Any]) -> None:
        x = float(params.get("x", 0))
        y = float(params.get("y", 0))

        if action_type == "click":
            modifier = str(params.get("modifier") or "").strip()
            if modifier:
                self._click_with_modifiers(x, y, modifier)
            else:
                self.click(x, y)
        elif action_type == "double_click":
            self.double_click(x, y)
        elif action_type == "right_click":
            self.right_click(x, y)
        elif action_type == "long_press":
            # Wire carries duration in ms (like wait); adapters take seconds.
            self.long_press(x, y, int(params.get("duration", 2000)) / 1000.0)
        elif action_type == "mouse_down":
            self.mouse_down(x, y)
        elif action_type == "mouse_up":
            # locate is optional: with resolved x/y, release there (drag-to-
            # target); without, release at the current cursor position.
            if params.get("x") is not None and params.get("y") is not None:
                self.mouse_up(x, y)
            else:
                self.mouse_up()
        elif action_type == "key_down":
            self.key_down(str(params.get("key", "")))
        elif action_type == "key_up":
            self.key_up(str(params.get("key", "")))
        elif action_type == "hover":
            self.hover(x, y)
        elif action_type == "type_text":
            # x/y are optional: with resolved coords, click-to-focus then type
            # (AI-located path); without, type into whatever already has focus
            # (direct path — no locating click, same convention as mouse_up).
            has_xy = params.get("x") is not None and params.get("y") is not None
            if params.get("clear_before_typing"):
                self.clear_text(x, y) if has_xy else self.clear_focused()
            text = str(params.get("text", ""))
            self.type_text(x, y, text) if has_xy else self.type_focused(text)
            if params.get("press_enter"):
                self.press_key("Enter")
        elif action_type == "clear_text":
            if params.get("x") is not None and params.get("y") is not None:
                self.clear_text(x, y)
            else:
                self.clear_focused()
        elif action_type == "press_key":
            key = str(params.get("key", ""))
            # duration_seconds > 0 turns the tap into a blocking hold (games
            # need W/A/S/D held for a fixed time). Dirty values (non-numeric
            # strings from the model) degrade to the instant tap, matching the
            # no-error posture of the whole duration path.
            try:
                duration = float(params.get("duration_seconds") or 0)
            except (TypeError, ValueError):
                duration = 0.0
            if duration > 0:
                self._press_key_held(key, min(duration, 10.0))
            else:
                self.press_key(key)
        elif action_type in ("scroll", "scroll_at"):
            self._scroll_action(action_type, params)
        elif action_type == "drag":
            self.drag(
                float(params.get("start_x", 0)), float(params.get("start_y", 0)),
                float(params.get("end_x", 0)), float(params.get("end_y", 0)),
            )
        elif action_type == "navigate":
            self.navigate(str(params.get("url", "")))
        elif action_type == "go_back":
            self.go_back()
        elif action_type == "wait":
            import time
            time.sleep(int(params.get("duration", 1000)) / 1000.0)
        elif action_type in ("done", "save_note"):
            pass
        else:
            raise ValueError(f"Unknown action type: {action_type}")
