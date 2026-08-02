"""Tests for adapter base execute() dispatch logic."""

import importlib.util
from unittest.mock import MagicMock

import pytest

from qirabot.adapters.base import DeviceAdapter, DeviceInfo


class FakeAdapter(DeviceAdapter):
    """Minimal concrete adapter for testing execute() dispatch."""

    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    @classmethod
    def accepts(cls, target):
        return False

    def screenshot(self, config=None):
        return b""

    def click(self, x, y):
        self.calls.append(("click", (x, y)))

    def double_click(self, x, y):
        self.calls.append(("double_click", (x, y)))

    def right_click(self, x, y):
        self.calls.append(("right_click", (x, y)))

    def hover(self, x, y):
        self.calls.append(("hover", (x, y)))

    def type_text(self, x, y, text):
        self.calls.append(("type_text", (x, y, text)))

    def type_focused(self, text):
        self.calls.append(("type_focused", (text,)))

    def clear_text(self, x, y):
        self.calls.append(("clear_text", (x, y)))

    def clear_focused(self):
        self.calls.append(("clear_focused", ()))

    def press_key(self, key):
        self.calls.append(("press_key", (key,)))

    def scroll(self, x, y, direction, distance):
        self.calls.append(("scroll", (x, y, direction, distance)))

    def drag(self, from_x, from_y, to_x, to_y):
        self.calls.append(("drag", (from_x, from_y, to_x, to_y)))

    def long_press(self, x, y, duration=2.0):
        self.calls.append(("long_press", (x, y, duration)))

    def mouse_down(self, x, y):
        self.calls.append(("mouse_down", (x, y)))

    def mouse_up(self, x=None, y=None):
        self.calls.append(("mouse_up", (x, y)))

    def key_down(self, key):
        self.calls.append(("key_down", (key,)))

    def key_up(self, key):
        self.calls.append(("key_up", (key,)))

    def device_info(self):
        return DeviceInfo(platform="test", width=100, height=100)


class TestControlsUserInput:
    """Only backends that drive the REAL mouse/keyboard flag themselves —
    the client keys the screen-edge "being controlled" glow off this."""

    def test_real_input_backends_flag_true(self):
        from qirabot.adapters.pyautogui_adapter import PyAutoGuiAdapter
        from qirabot.adapters.windows_adapter import WindowsAdapter

        assert PyAutoGuiAdapter.controls_user_input is True
        assert WindowsAdapter.controls_user_input is True

    def test_remote_protocol_backends_default_false(self):
        from qirabot.adapters.adb_adapter import AdbAdapter
        from qirabot.adapters.base import DeviceAdapter
        from qirabot.adapters.playwright_adapter import PlaywrightAdapter

        assert DeviceAdapter.controls_user_input is False
        assert PlaywrightAdapter.controls_user_input is False
        assert AdbAdapter.controls_user_input is False


class TestExecuteDispatch:
    def test_click(self):
        a = FakeAdapter()
        a.execute("click", {"x": 10, "y": 20})
        assert a.calls == [("click", (10.0, 20.0))]

    def test_double_click(self):
        a = FakeAdapter()
        a.execute("double_click", {"x": 10, "y": 20})
        assert a.calls == [("double_click", (10.0, 20.0))]

    def test_right_click(self):
        a = FakeAdapter()
        a.execute("right_click", {"x": 10, "y": 20})
        assert a.calls == [("right_click", (10.0, 20.0))]

    def test_long_press_default_duration(self):
        a = FakeAdapter()
        a.execute("long_press", {"x": 10, "y": 20})
        # No duration on the wire -> 2000ms default -> 2.0s.
        assert a.calls == [("long_press", (10.0, 20.0, 2.0))]

    def test_long_press_converts_ms_to_seconds(self):
        a = FakeAdapter()
        a.execute("long_press", {"x": 10, "y": 20, "duration": 600})
        assert a.calls == [("long_press", (10.0, 20.0, 0.6))]

    def test_mouse_down(self):
        a = FakeAdapter()
        a.execute("mouse_down", {"x": 10, "y": 20})
        assert a.calls == [("mouse_down", (10.0, 20.0))]

    def test_mouse_up_with_coords(self):
        a = FakeAdapter()
        a.execute("mouse_up", {"x": 10, "y": 20})
        # locate resolved -> release at the target point.
        assert a.calls == [("mouse_up", (10.0, 20.0))]

    def test_mouse_up_without_coords_releases_at_cursor(self):
        a = FakeAdapter()
        a.execute("mouse_up", {})
        # no x/y on the wire -> release at the current cursor (None, None).
        assert a.calls == [("mouse_up", (None, None))]

    def test_key_down(self):
        a = FakeAdapter()
        a.execute("key_down", {"key": "w"})
        assert a.calls == [("key_down", ("w",))]

    def test_key_up(self):
        a = FakeAdapter()
        a.execute("key_up", {"key": "w"})
        assert a.calls == [("key_up", ("w",))]

    def test_hover(self):
        a = FakeAdapter()
        a.execute("hover", {"x": 10, "y": 20})
        assert a.calls == [("hover", (10.0, 20.0))]

    def test_type_text_basic(self):
        a = FakeAdapter()
        a.execute("type_text", {"x": 10, "y": 20, "text": "hello"})
        assert a.calls == [("type_text", (10.0, 20.0, "hello"))]

    def test_type_text_with_press_enter(self):
        a = FakeAdapter()
        a.execute("type_text", {"x": 10, "y": 20, "text": "hello", "press_enter": True})
        assert a.calls == [
            ("type_text", (10.0, 20.0, "hello")),
            ("press_key", ("Enter",)),
        ]

    def test_type_text_with_clear_before_typing(self):
        a = FakeAdapter()
        a.execute("type_text", {"x": 10, "y": 20, "text": "hello", "clear_before_typing": True})
        assert a.calls == [
            ("clear_text", (10.0, 20.0)),
            ("type_text", (10.0, 20.0, "hello")),
        ]

    def test_type_text_with_both_flags(self):
        a = FakeAdapter()
        a.execute("type_text", {
            "x": 10, "y": 20, "text": "hello",
            "clear_before_typing": True, "press_enter": True,
        })
        assert a.calls == [
            ("clear_text", (10.0, 20.0)),
            ("type_text", (10.0, 20.0, "hello")),
            ("press_key", ("Enter",)),
        ]

    def test_type_text_without_coords_types_into_focused(self):
        a = FakeAdapter()
        a.execute("type_text", {"text": "hello"})
        # no x/y on the wire -> direct path: no locating click, no type_text.
        assert a.calls == [("type_focused", ("hello",))]

    def test_type_text_without_coords_with_flags(self):
        a = FakeAdapter()
        a.execute("type_text", {
            "text": "hello", "clear_before_typing": True, "press_enter": True,
        })
        assert a.calls == [
            ("clear_focused", ()),
            ("type_focused", ("hello",)),
            ("press_key", ("Enter",)),
        ]

    def test_clear_text(self):
        a = FakeAdapter()
        a.execute("clear_text", {"x": 10, "y": 20})
        assert a.calls == [("clear_text", (10.0, 20.0))]

    def test_clear_text_without_coords_clears_focused(self):
        a = FakeAdapter()
        a.execute("clear_text", {})
        assert a.calls == [("clear_focused", ())]

    def test_press_key(self):
        a = FakeAdapter()
        a.execute("press_key", {"key": "Enter"})
        assert a.calls == [("press_key", ("Enter",))]

    def test_scroll(self):
        a = FakeAdapter()
        a.execute("scroll", {"x": 0, "y": 0, "direction": "down", "distance": 300})
        assert a.calls == [("scroll", (0.0, 0.0, "down", 300))]

    def test_scroll_at(self):
        a = FakeAdapter()
        a.execute("scroll_at", {"x": 50, "y": 60, "direction": "up", "distance": 200})
        assert a.calls == [("scroll", (50.0, 60.0, "up", 200))]

    def test_scroll_amount_from_server(self):
        # The server sends distance as `amount` in pixels with no x/y; it must
        # be honored (converted to scroll units) rather than dropped to the
        # default of 3.
        a = FakeAdapter()
        a.execute("scroll", {"direction": "down", "amount": 500})
        assert a.calls == [("scroll", (0.0, 0.0, "down", 5))]

    def test_scroll_amount_rounds_up_to_one(self):
        a = FakeAdapter()
        a.execute("scroll", {"direction": "down", "amount": 30})
        assert a.calls == [("scroll", (0.0, 0.0, "down", 1))]

    def test_scroll_missing_amount_falls_back_to_default(self):
        a = FakeAdapter()
        a.execute("scroll", {"direction": "down"})
        assert a.calls == [("scroll", (0.0, 0.0, "down", 3))]

    def test_drag(self):
        a = FakeAdapter()
        a.execute("drag", {"start_x": 10, "start_y": 20, "end_x": 100, "end_y": 200})
        assert a.calls == [("drag", (10.0, 20.0, 100.0, 200.0))]

    def test_wait_is_noop(self):
        a = FakeAdapter()
        a.execute("wait", {"duration": 1000})
        assert a.calls == []

    def test_done_is_noop(self):
        a = FakeAdapter()
        a.execute("done", {"success": True, "result": "ok"})
        assert a.calls == []

    def test_save_note_is_noop(self):
        a = FakeAdapter()
        a.execute("save_note", {"content": "test"})
        assert a.calls == []

    def test_unknown_action_raises(self):
        a = FakeAdapter()
        with pytest.raises(ValueError, match="Unknown action type"):
            a.execute("nonexistent", {})


class TestPressKeyHeld:
    """press_key with duration_seconds: hold via key_down/key_up, degrade to a
    tap where the split primitives are missing, never leave a key stuck."""

    @pytest.fixture
    def sleeps(self, monkeypatch):
        recorded: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: recorded.append(s))
        return recorded

    def test_hold_single_key(self, sleeps):
        a = FakeAdapter()
        a.execute("press_key", {"key": "w", "duration_seconds": 2})
        assert a.calls == [("key_down", ("w",)), ("key_up", ("w",))]
        assert sleeps == [2.0]

    def test_hold_combo_wraps_modifiers(self, sleeps):
        a = FakeAdapter()
        a.execute("press_key", {"key": "shift+w", "duration_seconds": 1.5})
        assert a.calls == [
            ("key_down", ("shift",)),
            ("key_down", ("w",)),
            ("key_up", ("w",)),
            ("key_up", ("shift",)),
        ]
        assert sleeps == [1.5]

    def test_duration_clamped_to_ten_seconds(self, sleeps):
        a = FakeAdapter()
        a.execute("press_key", {"key": "w", "duration_seconds": 30})
        assert sleeps == [10.0]

    @pytest.mark.parametrize("duration", [0, None, "", "abc", -1])
    def test_zero_missing_or_dirty_duration_taps(self, sleeps, duration):
        a = FakeAdapter()
        params = {"key": "Enter"}
        if duration is not None:
            params["duration_seconds"] = duration
        a.execute("press_key", params)
        assert a.calls == [("press_key", ("Enter",))]
        assert sleeps == []

    def test_degrades_to_tap_without_key_down(self, sleeps):
        class NoHoldAdapter(FakeAdapter):
            def key_down(self, key):
                raise NotImplementedError

        a = NoHoldAdapter()
        a.execute("press_key", {"key": "shift+w", "duration_seconds": 2})
        assert a.calls == [("press_key", ("shift+w",))]
        assert sleeps == []

    def test_exception_during_hold_releases_pressed_keys(self, monkeypatch):
        def boom(_):
            raise RuntimeError("interrupted")

        monkeypatch.setattr("time.sleep", boom)
        a = FakeAdapter()
        with pytest.raises(RuntimeError, match="interrupted"):
            a.execute("press_key", {"key": "shift+w", "duration_seconds": 2})
        assert a.calls == [
            ("key_down", ("shift",)),
            ("key_down", ("w",)),
            ("key_up", ("w",)),
            ("key_up", ("shift",)),
        ]

    def test_failed_release_sweeps_all_inputs(self, sleeps):
        class StuckKeyAdapter(FakeAdapter):
            def key_up(self, key):
                raise RuntimeError("stuck")

            def release_all_inputs(self):
                self.calls.append(("release_all_inputs", ()))

        a = StuckKeyAdapter()
        a.execute("press_key", {"key": "w", "duration_seconds": 1})
        assert a.calls == [("key_down", ("w",)), ("release_all_inputs", ())]


class TestClickWithModifiers:
    """click with modifier: hold modifier key(s) around the click via
    key_down/key_up, degrade to a plain click where the split primitives are
    missing, never leave a modifier stuck."""

    @pytest.fixture
    def sleeps(self, monkeypatch):
        recorded: list[float] = []
        monkeypatch.setattr("time.sleep", lambda s: recorded.append(s))
        return recorded

    def test_single_modifier_wraps_click(self, sleeps):
        a = FakeAdapter()
        a.execute("click", {"x": 10, "y": 20, "modifier": "alt"})
        assert a.calls == [
            ("key_down", ("alt",)),
            ("click", (10.0, 20.0)),
            ("key_up", ("alt",)),
        ]
        # Guard sleeps before and after the click so frame-polling apps
        # sample the modifier as held through the whole button press.
        assert sleeps == [FakeAdapter._MODIFIER_LEAD, FakeAdapter._MODIFIER_TAIL]

    def test_env_overrides_modifier_pacing(self, sleeps, monkeypatch):
        # Games differ in how long their modifier "mode" transition takes;
        # the env knobs let users tune the hold without a code change.
        monkeypatch.setenv("QIRA_MODIFIER_LEAD", "0.8")
        monkeypatch.setenv("QIRA_MODIFIER_TAIL", "0.3")
        a = FakeAdapter()
        a.execute("click", {"x": 10, "y": 20, "modifier": "alt"})
        assert sleeps == [0.8, 0.3]

    def test_bad_env_value_falls_back_to_default(self, sleeps, monkeypatch):
        monkeypatch.setenv("QIRA_MODIFIER_LEAD", "not-a-number")
        a = FakeAdapter()
        a.execute("click", {"x": 10, "y": 20, "modifier": "alt"})
        assert sleeps == [FakeAdapter._MODIFIER_LEAD, FakeAdapter._MODIFIER_TAIL]

    def test_combo_modifiers_release_in_reverse(self, sleeps):
        a = FakeAdapter()
        a.execute("click", {"x": 10, "y": 20, "modifier": "ctrl+shift"})
        assert a.calls == [
            ("key_down", ("ctrl",)),
            ("key_down", ("shift",)),
            ("click", (10.0, 20.0)),
            ("key_up", ("shift",)),
            ("key_up", ("ctrl",)),
        ]

    @pytest.mark.parametrize("modifier", [None, "", "  "])
    def test_missing_or_blank_modifier_plain_click(self, sleeps, modifier):
        a = FakeAdapter()
        params = {"x": 10, "y": 20}
        if modifier is not None:
            params["modifier"] = modifier
        a.execute("click", params)
        assert a.calls == [("click", (10.0, 20.0))]
        assert sleeps == []

    def test_degrades_to_plain_click_without_key_down(self, sleeps):
        class NoHoldAdapter(FakeAdapter):
            def key_down(self, key):
                raise NotImplementedError

        a = NoHoldAdapter()
        a.execute("click", {"x": 10, "y": 20, "modifier": "alt"})
        assert a.calls == [("click", (10.0, 20.0))]
        assert sleeps == []

    def test_click_failure_releases_pressed_modifiers(self, sleeps):
        class BoomClickAdapter(FakeAdapter):
            def click(self, x, y):
                raise RuntimeError("click failed")

        a = BoomClickAdapter()
        with pytest.raises(RuntimeError, match="click failed"):
            a.execute("click", {"x": 10, "y": 20, "modifier": "ctrl+shift"})
        assert a.calls == [
            ("key_down", ("ctrl",)),
            ("key_down", ("shift",)),
            ("key_up", ("shift",)),
            ("key_up", ("ctrl",)),
        ]

    def test_failed_release_sweeps_all_inputs(self, sleeps):
        class StuckKeyAdapter(FakeAdapter):
            def key_up(self, key):
                raise RuntimeError("stuck")

            def release_all_inputs(self):
                self.calls.append(("release_all_inputs", ()))

        a = StuckKeyAdapter()
        a.execute("click", {"x": 10, "y": 20, "modifier": "alt"})
        assert a.calls == [
            ("key_down", ("alt",)),
            ("click", (10.0, 20.0)),
            ("release_all_inputs", ()),
        ]


class TestSettleOverride:
    """settle_seconds resolution: instance override beats the class default,
    and a screen-changing action sleeps that effective value."""

    def test_default_is_class_constant(self):
        a = FakeAdapter()
        assert a.settle_seconds == FakeAdapter._SETTLE_SECONDS

    def test_override_takes_precedence(self):
        a = FakeAdapter()
        a._SETTLE_SECONDS = 0.6  # pretend a platform default
        a._settle_override = 0.2
        assert a.settle_seconds == 0.2

    def test_override_zero_disables_settle(self, monkeypatch):
        import time

        a = FakeAdapter()
        a._SETTLE_SECONDS = 0.6
        a._settle_override = 0
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        a.execute("click", {"x": 1, "y": 2})
        assert sleeps == []  # 0 is falsy -> no sleep call

    def test_override_applied_on_screen_changing_action(self, monkeypatch):
        import time

        a = FakeAdapter()
        a._settle_override = 0.25
        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        a.execute("click", {"x": 1, "y": 2})
        assert sleeps == [0.25]


def _fake_pyautogui(*, screenshot_w: int, logical_w: int, logical_h: int | None = None):
    """Build a MagicMock standing in for the pyautogui module.

    screenshot().width / size().width drives the adapter's scale factor probe.
    """
    pag = MagicMock()
    pag.__name__ = "pyautogui"
    shot = MagicMock()
    shot.width = screenshot_w
    shot.height = (logical_h or logical_w) * (screenshot_w // logical_w)
    pag.screenshot.return_value = shot
    size = MagicMock()
    size.width = logical_w
    size.height = logical_h or logical_w
    pag.size.return_value = size
    return pag


class TestPyAutoGuiScaling:
    def _adapter(self, **kw):
        from qirabot.adapters.pyautogui_adapter import PyAutoGuiAdapter

        return PyAutoGuiAdapter(_fake_pyautogui(**kw))

    def test_retina_click_halves_coordinates(self):
        a = self._adapter(screenshot_w=2880, logical_w=1440)
        a.click(446, 388)
        a._pag.click.assert_called_once_with(223, 194)

    def test_non_retina_click_passes_through(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.click(446, 389)
        a._pag.click.assert_called_once_with(446, 389)

    def test_scale_factor_is_cached(self):
        a = self._adapter(screenshot_w=2880, logical_w=1440)
        a.click(100, 100)
        a.click(200, 200)
        # One probe screenshot for the factor; clicks reuse the cache.
        assert a._pag.screenshot.call_count == 1

    def test_drag_scales_both_endpoints(self):
        a = self._adapter(screenshot_w=2880, logical_w=1440)
        a.drag(100, 100, 300, 500)
        a._pag.moveTo.assert_called_once_with(50, 50)
        # drag receives the logical delta: (150-50, 250-50). button="left" is
        # required so pyautogui's default "primary" never reaches the macOS
        # backend's _dragTo() (which asserts left/middle/right).
        a._pag.drag.assert_called_once_with(100, 200, duration=0.5, button="left")

    def test_device_info_reports_physical_pixels_on_retina(self):
        # device_info must match the screenshot dimensions (physical), not
        # pyautogui's logical points, so callers that derive coordinates from it
        # (e.g. Qirabot.scroll's center anchor) stay in screenshot-pixel space.
        a = self._adapter(screenshot_w=2880, logical_w=1440, logical_h=900)
        info = a.device_info()
        assert (info.width, info.height) == (2880, 1800)

    def test_device_info_unchanged_on_non_retina(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440, logical_h=900)
        info = a.device_info()
        assert (info.width, info.height) == (1440, 900)

    def test_ascii_text_uses_typewrite(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.type_text(10, 20, "hello")
        a._pag.typewrite.assert_called_once_with("hello", interval=0.02)

    def test_non_ascii_text_pastes_via_clipboard(self, monkeypatch):
        import sys

        fake_clip = MagicMock()
        monkeypatch.setitem(sys.modules, "pyperclip", fake_clip)
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.type_text(10, 20, "打电话啊")
        fake_clip.copy.assert_called_once_with("打电话啊")
        a._pag.typewrite.assert_not_called()
        # paste hotkey fired with the platform modifier + 'v'
        assert a._pag.hotkey.call_args[0][-1] == "v"

    def test_mouse_down_holds_button_scaled(self):
        a = self._adapter(screenshot_w=2880, logical_w=1440)
        a.mouse_down(200, 400)
        # 2x display -> screenshot-pixel coords halved to logical points.
        a._pag.mouseDown.assert_called_once_with(100, 200)
        assert a._mouse_held is True

    def test_mouse_up_with_coords(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a._mouse_held = True
        a.mouse_up(50, 60)
        a._pag.mouseUp.assert_called_once_with(50, 60)
        assert a._mouse_held is False

    def test_mouse_up_without_coords_releases_at_cursor(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a._mouse_held = True
        a.mouse_up()
        a._pag.mouseUp.assert_called_once_with()
        assert a._mouse_held is False

    def test_key_down_up_tracks_held(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.key_down("w")
        a._pag.keyDown.assert_called_once_with("w")
        assert a._held_keys == {"w"}
        a.key_up("w")
        a._pag.keyUp.assert_called_once_with("w")
        assert a._held_keys == set()

    def test_key_down_normalizes_name(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.key_down("ArrowUp")
        # _norm_key maps ArrowUp -> up before holding.
        a._pag.keyDown.assert_called_once_with("up")
        assert a._held_keys == {"up"}

    def test_release_all_inputs_releases_held_key_and_mouse(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.key_down("w")
        a.key_down("shift")
        a.mouse_down(10, 20)
        a.release_all_inputs()
        # every held key released, mouse released at current cursor, state cleared.
        released = {c.args[0] for c in a._pag.keyUp.call_args_list}
        assert released == {"w", "shift"}
        a._pag.mouseUp.assert_called_once_with()
        assert a._held_keys == set()
        assert a._mouse_held is False

    def test_release_all_inputs_noop_when_nothing_held(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.release_all_inputs()
        a._pag.keyUp.assert_not_called()
        a._pag.mouseUp.assert_not_called()

    # pyautogui.scroll()'s unit is platform-dependent, so every scroll test pins
    # platform.system() — otherwise the expected click count flips with the OS
    # the suite runs on. macOS: lines (~3/notch); Windows: raw wheel delta
    # (120/notch); X11: one click/notch.
    @staticmethod
    def _pin_platform(monkeypatch, name):
        monkeypatch.setattr(
            "qirabot.adapters.pyautogui_adapter.platform.system", lambda: name
        )

    def test_scroll_without_xy_anchors_at_screen_center(self, monkeypatch):
        # Server plain scroll sends no x/y -> 0,0; without the center fallback
        # the scroll would land on the top-left corner and do nothing.
        self._pin_platform(monkeypatch, "Darwin")
        a = self._adapter(screenshot_w=1440, logical_w=1440, logical_h=900)
        a.scroll(0, 0, "down", 5)
        # macOS unit is lines (~3/notch): 5 notches -> 15 lines, centered.
        # center = (720, 450) in screenshot px == logical px here (scale 1.0)
        a._pag.scroll.assert_called_once_with(-15, x=720, y=450)

    def test_scroll_with_explicit_xy_is_respected(self, monkeypatch):
        self._pin_platform(monkeypatch, "Darwin")
        a = self._adapter(screenshot_w=1440, logical_w=1440, logical_h=900)
        a.scroll(100, 200, "up", 5)
        a._pag.scroll.assert_called_once_with(15, x=100, y=200)

    def test_server_scroll_path_honors_amount_and_centers(self, monkeypatch):
        # End-to-end through execute(): {direction, amount} only, as the server
        # sends it. amount 500 -> distance 5 -> 15 lines on macOS, centered.
        self._pin_platform(monkeypatch, "Darwin")
        a = self._adapter(screenshot_w=1440, logical_w=1440, logical_h=900)
        a.execute("scroll", {"direction": "down", "amount": 500})
        a._pag.scroll.assert_called_once_with(-15, x=720, y=450)

    def test_scroll_macos_does_not_move_cursor(self, monkeypatch):
        # macOS _scroll anchors at x/y itself, so the adapter must NOT moveTo.
        self._pin_platform(monkeypatch, "Darwin")
        a = self._adapter(screenshot_w=1440, logical_w=1440, logical_h=900)
        a.scroll(100, 200, "down", 5)
        a._pag.moveTo.assert_not_called()

    def test_scroll_windows_uses_wheel_delta_and_moves_first(self, monkeypatch):
        # Windows scroll() takes a raw wheel delta (120 == one notch) and ignores
        # x/y (no MOVE flag), so the adapter must scale by 120 AND move the cursor
        # to the anchor first, or the wheel lands wherever the cursor happens to be.
        self._pin_platform(monkeypatch, "Windows")
        a = self._adapter(screenshot_w=1440, logical_w=1440, logical_h=900)
        a.scroll(100, 200, "up", 5)
        a._pag.moveTo.assert_called_once_with(100, 200)
        a._pag.scroll.assert_called_once_with(600, x=100, y=200)  # 5 * 120

    def test_scroll_windows_horizontal_scales_and_signs(self, monkeypatch):
        self._pin_platform(monkeypatch, "Windows")
        a = self._adapter(screenshot_w=1440, logical_w=1440, logical_h=900)
        a.scroll(100, 200, "right", 5)
        a._pag.hscroll.assert_called_once_with(600, x=100, y=200)

    def test_scroll_linux_uses_one_click_per_notch(self, monkeypatch):
        # X11 scroll() is one wheel click (notch) per unit and already anchors at
        # x/y inside its own _scroll, so no 120x scaling and no extra moveTo.
        self._pin_platform(monkeypatch, "Linux")
        a = self._adapter(screenshot_w=1440, logical_w=1440, logical_h=900)
        a.scroll(100, 200, "down", 5)
        a._pag.scroll.assert_called_once_with(-5, x=100, y=200)
        a._pag.moveTo.assert_not_called()

    def test_press_key_single_special_is_normalized(self):
        # "Enter"/"ArrowDown" aren't pyautogui key names; press() would no-op.
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.press_key("Enter")
        a._pag.press.assert_called_once_with("enter")
        a._pag.hotkey.assert_not_called()

    def test_press_key_arrow_normalized(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.press_key("ArrowDown")
        a._pag.press.assert_called_once_with("down")

    def test_press_key_combo_uses_hotkey(self):
        # press() can't do combos; "ctrl+c" must go through hotkey().
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.press_key("ctrl+c")
        a._pag.hotkey.assert_called_once_with("ctrl", "c")
        a._pag.press.assert_not_called()

    def test_press_key_alt_tab_combo(self):
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.press_key("alt+tab")
        a._pag.hotkey.assert_called_once_with("alt", "tab")

    def test_screen_changing_action_settles(self, monkeypatch):
        import time

        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.execute("click", {"x": 10, "y": 20})
        assert sleeps == [a._SETTLE_SECONDS]

    def test_no_settle_action_does_not_wait(self, monkeypatch):
        import time

        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.execute("save_note", {"content": "x"})
        assert sleeps == []

    def test_hover_settles(self, monkeypatch):
        # hover reveals delayed UI (tooltip/submenu), so it must settle before the
        # next screenshot — it is NOT a _NO_SETTLE action.
        import time

        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        a = self._adapter(screenshot_w=1440, logical_w=1440)
        a.execute("hover", {"x": 10, "y": 20})
        assert sleeps == [a._SETTLE_SECONDS]


class TestSeleniumAdapterSettle:
    """Selenium's coordinate actions don't wait for the effects they trigger, so
    execute() adds a fixed settle before the ai() loop screenshots again."""

    def _adapter(self):
        from qirabot.adapters.selenium_adapter import SeleniumAdapter

        return SeleniumAdapter(MagicMock())

    def test_screen_changing_action_settles(self, monkeypatch):
        import time

        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        a = self._adapter()
        # amount 500 -> distance 5 -> scrollBy 500px, then settle.
        a.execute("scroll", {"direction": "down", "amount": 500})
        a._driver.execute_script.assert_called_once_with("window.scrollBy(0, 500)")
        assert sleeps == [a._SETTLE_SECONDS]

    def test_no_settle_action_does_not_wait(self, monkeypatch):
        import time

        sleeps: list[float] = []
        monkeypatch.setattr(time, "sleep", lambda s: sleeps.append(s))
        a = self._adapter()
        a.execute("done", {"success": True})
        assert sleeps == []


class TestSeleniumPressKey:
    """send_keys() treats its argument as literal text, so combos and special
    keys must be mapped to Keys.* and held modifiers via ActionChains."""

    # Unlike the other selenium tests (which drive a MagicMock driver), these
    # reach into the real selenium package for Keys/ActionChains. selenium is an
    # optional backend not installed in CI, so skip rather than error there.
    pytestmark = pytest.mark.skipif(
        importlib.util.find_spec("selenium") is None,
        reason="selenium is an optional backend; not installed",
    )

    def _adapter(self):
        from qirabot.adapters.selenium_adapter import SeleniumAdapter

        return SeleniumAdapter(MagicMock())

    def _patch_action_chains(self, monkeypatch):
        import selenium.webdriver.common.action_chains as acm

        fake = MagicMock()
        monkeypatch.setattr(acm, "ActionChains", fake)
        return fake

    def test_combo_holds_modifier(self, monkeypatch):
        from selenium.webdriver.common.keys import Keys

        fake = self._patch_action_chains(monkeypatch)
        self._adapter().press_key("ctrl+a")
        inst = fake.return_value
        inst.key_down.assert_called_once_with(Keys.CONTROL)
        inst.send_keys.assert_called_once_with("a")
        inst.key_up.assert_called_once_with(Keys.CONTROL)
        inst.perform.assert_called_once()

    def test_special_key_maps_to_keys_enum(self, monkeypatch):
        from selenium.webdriver.common.keys import Keys

        fake = self._patch_action_chains(monkeypatch)
        self._adapter().press_key("Enter")
        inst = fake.return_value
        inst.key_down.assert_not_called()
        inst.send_keys.assert_called_once_with(Keys.ENTER)
        inst.perform.assert_called_once()


class TestPlaywrightAdapterListener:
    """The adapter hooks the context's "page" event in __init__; close() must
    unhook it so the listener doesn't outlive the adapter and accumulate on the
    longer-lived context."""

    def test_close_removes_page_listener(self):
        from qirabot.adapters.playwright_adapter import PlaywrightAdapter

        page = MagicMock()
        context = MagicMock()
        page.context = context

        adapter = PlaywrightAdapter(page)
        assert context.on.call_count == 1
        event, handler = context.on.call_args.args
        assert event == "page"

        adapter.close()
        context.remove_listener.assert_called_once_with("page", handler)

    def test_close_swallows_remove_listener_errors(self):
        from qirabot.adapters.playwright_adapter import PlaywrightAdapter

        page = MagicMock()
        context = MagicMock()
        context.remove_listener.side_effect = Exception("context closed")
        page.context = context

        adapter = PlaywrightAdapter(page)
        adapter.close()  # must not raise even if the context is already gone


class TestBind:
    def _bound(self, bot):
        from qirabot.bound import _BoundQirabot

        return _BoundQirabot(bot, "T")

    def test_click_injects_target(self):
        bot = MagicMock()
        bot.click.return_value = "T"
        self._bound(bot).click("Login", retry=2)
        bot.click.assert_called_once_with(
            "T", "Login", modifier="", timeout=0.0, interval=2.0, wait="", retry=2,
            thinking_level="", language=""
        )

    def test_type_text_injects_target(self):
        bot = MagicMock()
        bot.type_text.return_value = "T"
        self._bound(bot).type_text("Email", "a@b.com", press_enter=True)
        bot.type_text.assert_called_once_with(
            "T",
            "Email",
            "a@b.com",
            press_enter=True,
            clear_before_typing=False,
            timeout=0.0,
            interval=2.0,
            wait="",
            retry=None,
            thinking_level="",
            language="",
        )

    def test_auto_rebind_follows_returned_target(self):
        bot = MagicMock()
        bot.click.return_value = "NEW_PAGE"
        b = self._bound(bot)
        assert b.click("x") == "NEW_PAGE"
        assert b._target == "NEW_PAGE"
        b.click("y")  # next call uses the new target
        assert bot.click.call_args_list[-1].args[0] == "NEW_PAGE"

    def test_non_rebinding_method_keeps_target(self):
        bot = MagicMock()
        bot.extract.return_value = "text"
        b = self._bound(bot)
        assert b.extract("get title") == "text"
        assert b._target == "T"  # extract doesn't re-bind

    def test_lifecycle_explicit_delegation(self):
        bot = MagicMock()
        bot.task_id = "task-123"
        b = self._bound(bot)
        b.close()
        bot.close.assert_called_once_with()
        assert b.task_id == "task-123"

    def test_unknown_attr_delegates_via_getattr(self):
        bot = MagicMock()
        b = self._bound(bot)
        b.launch_app("WeChat", wait=3)
        bot.launch_app.assert_called_once_with("WeChat", wait=3)

    def test_context_manager_delegates_and_returns_self(self):
        bot = MagicMock()
        b = self._bound(bot)
        with b as entered:
            assert entered is b
        bot.__enter__.assert_called_once()
        bot.__exit__.assert_called_once()

    def test_bind_parity_covers_all_target_methods(self):
        import inspect

        from qirabot.bound import _BoundQirabot
        from qirabot.client import Qirabot

        # Compare the call contract only: name, kind (positional vs
        # keyword-only) and default. Annotations are deliberately excluded —
        # under ``from __future__ import annotations`` they are raw source
        # strings, so an equivalent spelling (e.g. ``Callable[[StepResult]...``
        # vs ``Callable[["StepResult"]...``) would false-fail without changing
        # how the method is called.
        def call_contract(params):
            return [(p.name, p.kind, p.default) for p in params]

        for name in dir(Qirabot):
            if name.startswith("_") or name == "bind":
                continue  # bind() takes a target but creates the proxy, not proxied
            attr = inspect.getattr_static(Qirabot, name)
            if not inspect.isfunction(attr):
                continue
            params = list(inspect.signature(attr).parameters.values())
            if not (len(params) >= 2 and params[1].name == "target"):
                continue
            assert hasattr(_BoundQirabot, name), f"facade missing proxy for {name}"

            # The proxy must mirror the source signature exactly, minus the
            # injected ``target``. A plain hasattr check misses param drift —
            # e.g. adding a kwarg to Qirabot.click but forgetting the wrapper.
            bound_params = list(
                inspect.signature(getattr(_BoundQirabot, name)).parameters.values()
            )
            expected = call_contract(params[2:])  # drop self + target
            actual = call_contract(bound_params[1:])  # drop self
            assert actual == expected, (
                f"_BoundQirabot.{name} signature drifted from Qirabot.{name}:\n"
                f"  expected (minus target): {expected}\n"
                f"  actual:                  {actual}"
            )


class TestCustomAdapterHooks:
    """Third-party backend hooks: detect() passthrough for adapter instances,
    and register_adapter() for auto-detection of custom targets."""

    @pytest.fixture(autouse=True)
    def isolated_registry(self, monkeypatch):
        """Run each test against a copy of the adapter registry so
        register_adapter() calls can't leak into other tests."""
        from qirabot.adapters import auto

        monkeypatch.setattr(auto, "_ADAPTER_CLASSES", list(auto._ADAPTER_CLASSES))

    def test_adapter_instance_passes_through_detect(self):
        from qirabot.adapters.auto import detect

        a = FakeAdapter()
        assert detect(a) is a

    def test_current_target_defaults_to_self(self):
        # _result() reads current_target after every action; the base default
        # must not raise for adapters passed straight to bind().
        a = FakeAdapter()
        assert a.current_target is a

    def test_base_accepts_defaults_to_false(self):
        assert DeviceAdapter.accepts(object()) is False

    def _sentinel_pair(self):
        class SentinelTarget:
            pass

        class SentinelAdapter(FakeAdapter):
            def __init__(self, target):
                super().__init__()
                self.target = target

            @classmethod
            def accepts(cls, target):
                return isinstance(target, SentinelTarget)

        return SentinelTarget, SentinelAdapter

    def test_registered_adapter_is_detected(self):
        from qirabot.adapters.auto import detect, register_adapter

        SentinelTarget, SentinelAdapter = self._sentinel_pair()
        register_adapter(SentinelAdapter)
        target = SentinelTarget()
        adapter = detect(target)
        assert isinstance(adapter, SentinelAdapter)
        assert adapter.target is target

    def test_registered_adapter_checked_before_builtins(self):
        from qirabot.adapters import auto

        _, SentinelAdapter = self._sentinel_pair()
        auto.register_adapter(SentinelAdapter)
        assert auto._ADAPTER_CLASSES[0] is SentinelAdapter

    def test_duplicate_registration_is_noop(self):
        from qirabot.adapters import auto

        _, SentinelAdapter = self._sentinel_pair()
        auto.register_adapter(SentinelAdapter)
        auto.register_adapter(SentinelAdapter)
        assert auto._ADAPTER_CLASSES.count(SentinelAdapter) == 1

    @pytest.mark.parametrize("bad", [object, "adapter", None])
    def test_non_adapter_class_raises(self, bad):
        from qirabot.adapters.auto import register_adapter

        with pytest.raises(TypeError, match="DeviceAdapter subclass"):
            register_adapter(bad)

    def test_adapter_instance_raises_not_registered(self):
        from qirabot.adapters.auto import register_adapter

        with pytest.raises(TypeError, match="DeviceAdapter subclass"):
            register_adapter(FakeAdapter())

    def test_registered_airtest_adapter_shadows_tombstone(self):
        # The whole point of the escape hatch: a user-supplied airtest adapter
        # must win over the removal error for airtest targets.
        from qirabot.adapters.auto import detect, register_adapter

        class AirtestLikeAdapter(FakeAdapter):
            def __init__(self, target):
                super().__init__()

            @classmethod
            def accepts(cls, target):
                return type(target).__module__.startswith("airtest.")

        class FakeDevice:
            pass

        FakeDevice.__module__ = "airtest.core.device"
        register_adapter(AirtestLikeAdapter)
        assert isinstance(detect(FakeDevice()), AirtestLikeAdapter)

    def test_exported_from_package_root(self):
        import qirabot

        assert qirabot.register_adapter is not None
        assert "register_adapter" in qirabot.__all__


class TestAirtestTombstone:
    """2.0 removed airtest; detect() must recognize its targets by module-name
    strings (zero imports) and answer with the migration guidance."""

    def _assert_tombstone(self, target):
        from qirabot.adapters.auto import detect

        with pytest.raises(TypeError) as ei:
            detect(target)
        msg = str(ei.value)
        assert "removed in qirabot 2.0" in msg
        assert "qirabot.AdbDevice" in msg
        assert "qirabot.Window" in msg
        assert "qirabot.WdaClient" in msg
        assert "qirabot<2.0" in msg

    def test_device_instance(self):
        class FakeDevice:
            pass

        FakeDevice.__module__ = "airtest.core.device"
        self._assert_tombstone(FakeDevice())

    def test_api_module(self):
        import types

        self._assert_tombstone(types.ModuleType("airtest.core.api"))

    def test_g_global(self):
        class G:
            pass

        G.__module__ = "airtest.core.helper"
        self._assert_tombstone(G)  # the class itself, as airtest exposes it

    def test_unrelated_target_gets_plain_unsupported_error(self):
        from qirabot.adapters.auto import detect

        with pytest.raises(TypeError) as ei:
            detect(object())
        assert "removed in qirabot 2.0" not in str(ei.value)
        assert "Unsupported target type" in str(ei.value)
