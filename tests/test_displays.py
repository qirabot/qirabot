"""Display selection: Qirabot(display=N) / QIRA_DISPLAY / desktop --display N.

One numbering (1-based, 1 = primary) drives the pyautogui adapter's capture
and input offset, the host-screen recorder and the overlay helper. These
tests pin each piece with fakes — no real displays, captures or GUI.
"""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from qirabot import displays
from qirabot.displays import Display, parse_display

PRIMARY = Display(index=1, x=0, y=0, width=1920, height=1080, primary=True, name="DELL", id=3)
# A Retina laptop panel LEFT of the primary: negative origin, like the
# reporter's setup.
LEFT = Display(
    index=2, x=-1710, y=0, width=1710, height=1112, primary=False,
    name="Built-in", id=1, scale=2.0,
)


def _fake_pag(width: int = 1920, height: int = 1080) -> MagicMock:
    pag = MagicMock()
    pag.__name__ = "pyautogui"
    size = MagicMock()
    size.width, size.height = width, height
    pag.size.return_value = size
    return pag


def _shot(width: int, height: int) -> MagicMock:
    img = MagicMock()
    img.width, img.height = width, height
    return img


# --------------------------------------------------------------------------- #
# parse_display
# --------------------------------------------------------------------------- #
class TestParseDisplay:
    @pytest.mark.parametrize("value", [None, "", "  "])
    def test_unset(self, value):
        assert parse_display(value) is None

    @pytest.mark.parametrize("value,expected", [(1, 1), (2, 2), ("3", 3), (" 2 ", 2)])
    def test_valid(self, value, expected):
        assert parse_display(value) == expected

    @pytest.mark.parametrize("value", [0, -1, "0", "two", 1.5, True, "1.0"])
    def test_invalid_names_the_source(self, value):
        with pytest.raises(ValueError, match="QIRA_DISPLAY"):
            parse_display(value, "QIRA_DISPLAY")


class TestGetDisplay:
    def test_not_found_lists_available(self, monkeypatch):
        monkeypatch.setattr(displays, "list_displays", lambda: [PRIMARY, LEFT])
        from qirabot.exceptions import QirabotError

        with pytest.raises(QirabotError, match="display 3 not found") as exc:
            displays.get_display(3)
        assert "1710x1112" in str(exc.value)
        assert exc.value.code == "display.not_found"

    def test_describe_marks_primary(self):
        assert "[primary]" in PRIMARY.describe()
        assert "@ (-1710, 0)" in LEFT.describe()


# --------------------------------------------------------------------------- #
# PyAutoGuiAdapter with a selected display
# --------------------------------------------------------------------------- #
class TestAdapterDisplay:
    def _adapter(self, monkeypatch, display=LEFT, shot=(3420, 2224)):
        from qirabot.adapters import pyautogui_adapter
        from qirabot.adapters.pyautogui_adapter import PyAutoGuiAdapter

        monkeypatch.setattr(pyautogui_adapter.sys, "platform", "darwin")
        a = PyAutoGuiAdapter(_fake_pag(), display)
        grabs: list[Display] = []

        def fake_grab(d):
            grabs.append(d)
            return _shot(*shot)

        monkeypatch.setattr(a, "_grab_display", fake_grab)
        return a, grabs

    def test_click_offsets_by_display_origin_and_scale(self, monkeypatch):
        a, grabs = self._adapter(monkeypatch)
        # Retina 2x capture of a 1710pt-wide display left of the primary:
        # screenshot pixel (200, 100) -> point (100, 50) -> global (-1610, 50).
        a.click(200, 100)
        a._pag.click.assert_called_once_with(-1610, 50)
        assert grabs == [LEFT]  # captured the selected display, not pyautogui's

    def test_screenshot_never_uses_pyautogui_capture(self, monkeypatch):
        a, grabs = self._adapter(monkeypatch)
        monkeypatch.setattr(a, "_encode_image", lambda img, cfg: b"png")
        assert a.screenshot() == b"png"
        a._pag.screenshot.assert_not_called()

    def test_device_info_is_the_display_in_pixels(self, monkeypatch):
        a, _ = self._adapter(monkeypatch)
        info = a.device_info()
        assert (info.width, info.height) == (3420, 2224)

    def test_default_scroll_anchor_is_display_center(self, monkeypatch):
        a, _ = self._adapter(monkeypatch)
        a.scroll(0, 0, "down", 1)
        _, kwargs = a._pag.scroll.call_args
        assert (kwargs["x"], kwargs["y"]) == (-1710 + 855, 556)

    def test_drag_moves_in_global_coordinates(self, monkeypatch):
        a, _ = self._adapter(monkeypatch)
        a.drag(0, 0, 200, 200)
        a._pag.moveTo.assert_called_once_with(-1710, 0)
        a._pag.drag.assert_called_once_with(100, 100, duration=0.5, button="left")

    def test_no_display_keeps_legacy_path(self, monkeypatch):
        from qirabot.adapters.pyautogui_adapter import PyAutoGuiAdapter

        pag = _fake_pag(1440, 900)
        pag.screenshot.return_value = _shot(2880, 1800)
        a = PyAutoGuiAdapter(pag)
        a.click(446, 388)
        pag.click.assert_called_once_with(223, 194)

    def test_linux_rejects_other_displays(self, monkeypatch):
        from qirabot.adapters.pyautogui_adapter import PyAutoGuiAdapter
        from qirabot.exceptions import QirabotError

        monkeypatch.setattr(displays.sys, "platform", "linux")
        with pytest.raises(QirabotError, match="macOS and Windows only"):
            PyAutoGuiAdapter(_fake_pag(), LEFT)

    def _mac(self, monkeypatch, run, permitted=True, cg=None):
        from qirabot.adapters import pyautogui_adapter
        from qirabot.adapters.pyautogui_adapter import PyAutoGuiAdapter

        monkeypatch.setattr(pyautogui_adapter.sys, "platform", "darwin")
        monkeypatch.setattr(pyautogui_adapter.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(pyautogui_adapter.subprocess, "run", run)
        monkeypatch.setattr(pyautogui_adapter, "_macos_capture_permitted", lambda: permitted)
        monkeypatch.setattr(pyautogui_adapter, "_cg_display_image", lambda did: cg)
        return PyAutoGuiAdapter(_fake_pag(), LEFT)

    @staticmethod
    def _writes_png(seen):
        def fake_run(cmd, **kw):
            seen.append(cmd)
            from PIL import Image

            Image.new("RGB", (8, 4)).save(cmd[-1])
            return subprocess.CompletedProcess(cmd, 0)

        return fake_run

    def test_macos_capture_uses_global_region_first(self, monkeypatch):
        seen: list = []
        img = self._mac(monkeypatch, self._writes_png(seen))._grab()
        assert seen[0][:4] == ["screencapture", "-x", "-R", "-1710,0,1710,1112"]
        assert img.size == (8, 4)

    def test_falls_back_to_dash_d_and_remembers(self, monkeypatch):
        seen: list = []
        ok = self._writes_png(seen)

        def run(cmd, **kw):
            if "-R" in cmd:
                seen.append(cmd)
                raise subprocess.CalledProcessError(1, cmd, stderr=b"bad rect")
            return ok(cmd, **kw)

        a = self._mac(monkeypatch, run)
        a._grab()
        a._grab()
        flags = [c[2] for c in seen]
        # second capture goes straight to the strategy that worked
        assert flags == ["-R", "-D2", "-D2"]

    def test_falls_back_to_coregraphics(self, monkeypatch):
        def refuse(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)

        sentinel = _shot(10, 10)
        assert self._mac(monkeypatch, refuse, cg=sentinel)._grab() is sentinel

    def test_macos_capture_refusal_names_the_permission(self, monkeypatch):
        def refuse(cmd, **kw):
            raise subprocess.CalledProcessError(1, cmd)

        with pytest.raises(RuntimeError, match="Screen Recording"):
            self._mac(monkeypatch, refuse, permitted=False)._grab()

    def test_non_permission_failure_shows_the_real_error(self, monkeypatch):
        """Permission granted but capture still fails: must NOT send the
        user to System Settings — show screencapture's own stderr."""

        def fail(cmd, **kw):
            raise subprocess.CalledProcessError(
                1, cmd, stderr=b"could not create image from display"
            )

        with pytest.raises(RuntimeError) as exc:
            self._mac(monkeypatch, fail, permitted=True)._grab()
        assert "permission is granted" in str(exc.value)
        assert "could not create image from display" in str(exc.value)
        assert "System Settings" not in str(exc.value)

    def test_windows_input_runs_in_dpi_context(self, monkeypatch):
        """Windows display rects are per-monitor-DPI physical pixels; the
        cursor calls must run in that same thread context."""
        import contextlib

        from qirabot import windows
        from qirabot.adapters import pyautogui_adapter
        from qirabot.adapters.pyautogui_adapter import PyAutoGuiAdapter

        monkeypatch.setattr(pyautogui_adapter.sys, "platform", "win32")
        state = {"inside": False, "clicked_inside": None}

        @contextlib.contextmanager
        def fake_ctx():
            state["inside"] = True
            try:
                yield
            finally:
                state["inside"] = False

        monkeypatch.setattr(windows, "dpi_awareness", fake_ctx)
        win = Display(index=2, x=1920, y=0, width=2560, height=1440, primary=False)
        a = PyAutoGuiAdapter(_fake_pag(), win)
        monkeypatch.setattr(a, "_grab_display", lambda d: _shot(2560, 1440))
        a._pag.click.side_effect = lambda *_: state.update(clicked_inside=state["inside"])
        a.click(100, 50)
        a._pag.click.assert_called_once_with(2020, 50)
        assert state["clicked_inside"] is True


# --------------------------------------------------------------------------- #
# Recording
# --------------------------------------------------------------------------- #
class TestRecorderDisplay:
    def _cmd(self, monkeypatch, tmp_path, plat, **kw):
        from qirabot import recording

        monkeypatch.setattr(recording.sys, "platform", plat)
        seen = {}
        monkeypatch.setattr(
            recording, "_detect_screen_index",
            lambda f, d=None: seen.setdefault("display", d) and "9" or "9",
        )
        rec = recording.ScreenRecorder(str(tmp_path / "r.mp4"), **kw)
        cmd, describe = rec._build_cmd("ffmpeg")
        return cmd, describe, seen

    def test_windows_display_becomes_desktop_crop(self, monkeypatch, tmp_path):
        d = Display(index=2, x=-1921, y=0, width=1921, height=1081, primary=False)
        cmd, describe, _ = self._cmd(monkeypatch, tmp_path, "win32", display=d)
        assert cmd[cmd.index("-offset_x") + 1] == "-1921"
        assert cmd[cmd.index("-video_size") + 1] == "1920x1080"  # even for yuv420p
        assert "display 2" in describe

    def test_windows_explicit_region_wins(self, monkeypatch, tmp_path):
        d = Display(index=2, x=-1920, y=0, width=1920, height=1080, primary=False)
        cmd, _, _ = self._cmd(
            monkeypatch, tmp_path, "win32", display=d, region=(10, 20, 300, 200)
        )
        assert cmd[cmd.index("-offset_x") + 1] == "10"

    def test_macos_resolves_the_display_screen_device(self, monkeypatch, tmp_path):
        cmd, _, seen = self._cmd(monkeypatch, tmp_path, "darwin", display=LEFT)
        assert seen["display"] == 2
        assert "9:none" in cmd

    def test_manager_passes_display_only_when_selected(self, monkeypatch, tmp_path):
        from qirabot import recording

        made = []

        class _Rec:
            def __init__(self, output, **kw):
                made.append(kw)

            def start(self):
                return False

        monkeypatch.setattr(recording, "ScreenRecorder", _Rec)
        cfg = recording.RecordConfig(record=True)
        recording.RecordingManager(cfg, output_dir=lambda: str(tmp_path)).start()
        recording.RecordingManager(
            cfg, output_dir=lambda: str(tmp_path), display=LEFT
        ).start()
        assert "display" not in made[0]
        assert made[1]["display"] is LEFT


# --------------------------------------------------------------------------- #
# Overlay
# --------------------------------------------------------------------------- #
class TestOverlayDisplay:
    def test_helper_args_carry_the_display(self):
        from qirabot.overlay import Overlay

        args = Overlay(display=LEFT)._helper_args()
        payload = json.loads(args[args.index("--display") + 1])
        assert payload == {"index": 2, "id": 1, "x": -1710, "y": 0, "w": 1710, "h": 1112}

    def test_no_display_no_arg(self):
        from qirabot.overlay import Overlay

        assert "--display" not in Overlay()._helper_args()

    def test_set_display_ignored_once_spawned(self):
        from qirabot.overlay import Overlay

        ov = Overlay()
        ov._proc = MagicMock()
        ov.set_display(LEFT)
        assert ov._display is None

    @pytest.mark.parametrize(
        "argv,expected",
        [
            (["--display", '{"id": 1, "x": -1710, "y": 0, "w": 1710, "h": 1112}'], -1710),
            ([], None),
            (["--display"], None),
            (["--display", "not json"], None),
            (["--display", '{"x": 1}'], None),
        ],
    )
    def test_helper_parses_display_arg(self, argv, expected):
        from qirabot._overlay_helper import _parse_display

        parsed = _parse_display(argv)
        assert (parsed["x"] if parsed else None) == expected


# --------------------------------------------------------------------------- #
# Client wiring
# --------------------------------------------------------------------------- #
class TestClientDisplay:
    @pytest.fixture(autouse=True)
    def _two_displays(self, monkeypatch):
        monkeypatch.delenv("QIRA_DISPLAY", raising=False)
        monkeypatch.setattr(displays, "list_displays", lambda: [PRIMARY, LEFT])
        monkeypatch.setattr(displays.sys, "platform", "darwin")

    def test_explicit_display_reaches_adapter_and_overlay(self, monkeypatch, tmp_path):
        from qirabot import Qirabot

        bot = Qirabot(display=2, report=False, overlay=True)
        try:
            set_calls = []
            monkeypatch.setattr(bot._overlay, "set_display", set_calls.append)
            adapter = bot._get_adapter(_fake_pag())
            assert adapter.display is LEFT
            assert set_calls == [LEFT]
            assert bot._recording.display is LEFT
        finally:
            bot.close()

    def test_explicit_display_rejects_non_desktop_target(self):
        from qirabot import Qirabot
        from qirabot.adapters.base import DeviceAdapter
        from qirabot.exceptions import QirabotError

        bot = Qirabot(display=2, report=False)
        try:
            with pytest.raises(QirabotError, match="only applies to the desktop"):
                bot._get_adapter(MagicMock(spec=DeviceAdapter))
        finally:
            bot.close()

    def test_env_display_is_ignored_for_non_desktop_target(self, monkeypatch):
        from qirabot import Qirabot
        from qirabot.adapters.base import DeviceAdapter

        monkeypatch.setenv("QIRA_DISPLAY", "2")
        bot = Qirabot(report=False)
        try:
            target = MagicMock(spec=DeviceAdapter)
            assert bot._get_adapter(target) is target
            assert bot._get_adapter(_fake_pag()).display is LEFT
        finally:
            bot.close()

    def test_explicit_missing_display_raises(self):
        from qirabot import Qirabot
        from qirabot.exceptions import QirabotError

        with pytest.raises(QirabotError, match="display 5 not found"):
            Qirabot(display=5, report=False)

    @pytest.mark.parametrize("value", ["5", "abc", "0"])
    def test_bad_env_warns_and_uses_primary(self, monkeypatch, caplog, value):
        from qirabot import Qirabot

        monkeypatch.setenv("QIRA_DISPLAY", value)
        with caplog.at_level("WARNING", logger="qirabot"):
            bot = Qirabot(report=False)
        try:
            assert bot._display is None
            assert "QIRA_DISPLAY ignored" in caplog.text
        finally:
            bot.close()

    def test_linux_explicit_other_display_unsupported(self, monkeypatch):
        from qirabot import Qirabot
        from qirabot.exceptions import QirabotError

        monkeypatch.setattr(displays.sys, "platform", "linux")
        with pytest.raises(QirabotError, match="macOS and Windows only"):
            Qirabot(display=2, report=False)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _invoke(args):
    from qirabot.cli.main import cli

    return CliRunner().invoke(cli, args)


class TestCliDisplay:
    def _capture(self, monkeypatch):
        from qirabot.cli import main

        captured = {}

        def fake_make_bot(ctx, **kwargs):
            captured.update(kwargs)
            return MagicMock(name="bot")

        monkeypatch.setattr(main, "_make_bot", fake_make_bot)
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)
        monkeypatch.setattr(main, "require", lambda *a, **k: _fake_pag())
        return captured

    def test_display_is_threaded_to_the_sdk(self, monkeypatch):
        captured = self._capture(monkeypatch)
        result = _invoke(["desktop", "do it", "--display", "2"])
        assert result.exit_code == 0, result.output
        assert captured["display"] == 2

    def test_omitted_display_leaves_it_to_the_sdk(self, monkeypatch):
        captured = self._capture(monkeypatch)
        result = _invoke(["desktop", "do it"])
        assert result.exit_code == 0, result.output
        assert captured["display"] is None

    def test_display_zero_is_rejected(self, monkeypatch):
        self._capture(monkeypatch)
        result = _invoke(["desktop", "do it", "--display", "0"])
        assert result.exit_code == 2

    @pytest.mark.parametrize("bind", [["--window-title", "Game"], ["--hwnd", "42"]])
    def test_display_conflicts_with_window_binding(self, monkeypatch, bind):
        self._capture(monkeypatch)
        result = _invoke(["desktop", "do it", "--display", "2", *bind])
        assert result.exit_code == 2
        assert "--display" in result.output

    def test_ios_has_no_display_option(self):
        result = _invoke(["ios", "do it", "--display", "2"])
        assert result.exit_code == 2
        assert "No such option" in result.output

    def test_displays_command_lists_them(self, monkeypatch):
        monkeypatch.setattr(displays, "list_displays", lambda: [PRIMARY, LEFT])
        result = _invoke(["displays"])
        assert result.exit_code == 0, result.output
        lines = result.output.splitlines()
        assert lines[0].startswith("1*") and "[primary]" in lines[0]
        assert lines[1].startswith("2 ") and "-1710" in lines[1]
        assert "--display N" in result.output

    def test_displays_json(self, monkeypatch):
        monkeypatch.setattr(displays, "list_displays", lambda: [PRIMARY, LEFT])
        result = _invoke(["displays", "--json"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert [d["index"] for d in data] == [1, 2]
        assert data[1]["x"] == -1710
