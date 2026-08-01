"""Tests for the direct-adb backend (AdbDevice + AdbAdapter).

Every subprocess goes through AdbDevice._run, so the tests monkeypatch that
single seam with scripted outputs and assert on the exact adb argv/shell
command lines the backend emits.
"""

from __future__ import annotations

import base64
import io
import subprocess
from contextlib import contextmanager

import pytest

from qirabot.adapters.adb_adapter import AdbAdapter, _ascii_typeable
from qirabot.adb import AdbDevice, _which_adb
from qirabot.exceptions import QirabotError


def proc(
    stdout: bytes = b"", rc: int = 0, stderr: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(["adb"], rc, stdout=stdout, stderr=stderr)


def scripted_device(monkeypatch, responses=None, serial="emulator-5554"):
    """AdbDevice whose _run records calls and replays canned responses.

    ``responses`` maps a prefix of the args list (joined by space) to stdout
    bytes; unmatched calls return empty success.
    """
    dev = AdbDevice(serial=serial)
    dev._serial_checked = True
    dev._adb_path = "/fake/adb"
    calls: list[list[str]] = []

    def fake_run(args, *, scoped=True, timeout=30.0, check=True):
        calls.append(list(args))
        joined = " ".join(args)
        for prefix, out in (responses or {}).items():
            if joined.startswith(prefix):
                return proc(out if isinstance(out, bytes) else out.encode())
        return proc()

    monkeypatch.setattr(dev, "_run", fake_run)
    return dev, calls


def shell_calls(calls):
    return [" ".join(c[1:]) for c in calls if c and c[0] == "shell"]


# ---------------------------------------------------------------------------
# adb discovery
# ---------------------------------------------------------------------------


class TestAdbDiscovery:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        fake = tmp_path / "adb"
        fake.write_text("")
        monkeypatch.setenv("QIRA_ADB_PATH", str(fake))
        assert _which_adb() == str(fake)

    def test_env_override_missing_file_is_none(self, monkeypatch, tmp_path):
        monkeypatch.setenv("QIRA_ADB_PATH", str(tmp_path / "nope"))
        assert _which_adb() is None

    def test_path_lookup(self, monkeypatch):
        monkeypatch.delenv("QIRA_ADB_PATH", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/adb")
        assert _which_adb() == "/usr/bin/adb"

    def test_android_home_fallback(self, monkeypatch, tmp_path):
        monkeypatch.delenv("QIRA_ADB_PATH", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        tools = tmp_path / "platform-tools"
        tools.mkdir()
        adb = tools / ("adb.exe" if __import__("sys").platform == "win32" else "adb")
        adb.write_text("")
        monkeypatch.setenv("ANDROID_HOME", str(tmp_path))
        assert _which_adb() == str(adb)

    def test_not_found_raises_actionable_error(self, monkeypatch):
        monkeypatch.delenv("QIRA_ADB_PATH", raising=False)
        monkeypatch.delenv("ANDROID_HOME", raising=False)
        monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
        monkeypatch.setattr("shutil.which", lambda name: None)
        with pytest.raises(QirabotError) as ei:
            _ = AdbDevice().adb_path
        assert ei.value.code == "adb.not_found"
        assert "platform-tools" in str(ei.value)


# ---------------------------------------------------------------------------
# serial resolution
# ---------------------------------------------------------------------------


def resolving_device(monkeypatch, devices_output: str, serial: str | None = None):
    dev = AdbDevice(serial=serial)
    dev._adb_path = "/fake/adb"

    def fake_run(args, *, scoped=True, timeout=30.0, check=True):
        assert args == ["devices"] and not scoped
        return proc(devices_output.encode())

    monkeypatch.setattr(dev, "_run", fake_run)
    return dev


class TestSerialResolution:
    def test_single_device_auto_picked(self, monkeypatch):
        dev = resolving_device(monkeypatch, "List of devices attached\nemulator-5554\tdevice\n")
        assert dev.serial == "emulator-5554"

    def test_no_devices(self, monkeypatch):
        dev = resolving_device(monkeypatch, "List of devices attached\n")
        with pytest.raises(QirabotError) as ei:
            _ = dev.serial
        assert ei.value.code == "adb.no_devices"

    def test_multiple_devices(self, monkeypatch):
        dev = resolving_device(
            monkeypatch,
            "List of devices attached\nemulator-5554\tdevice\n192.168.1.8:5555\tdevice\n",
        )
        with pytest.raises(QirabotError) as ei:
            _ = dev.serial
        assert ei.value.code == "adb.multiple_devices"
        assert "emulator-5554" in str(ei.value)

    def test_unauthorized(self, monkeypatch):
        dev = resolving_device(
            monkeypatch, "List of devices attached\nemulator-5554\tunauthorized\n"
        )
        with pytest.raises(QirabotError) as ei:
            _ = dev.serial
        assert ei.value.code == "adb.unauthorized"

    def test_offline(self, monkeypatch):
        dev = resolving_device(monkeypatch, "List of devices attached\nemulator-5554\toffline\n")
        with pytest.raises(QirabotError) as ei:
            _ = dev.serial
        assert ei.value.code == "adb.offline"

    def test_explicit_serial_not_connected(self, monkeypatch):
        dev = resolving_device(
            monkeypatch,
            "List of devices attached\nemulator-5554\tdevice\n",
            serial="deadbeef",
        )
        with pytest.raises(QirabotError) as ei:
            _ = dev.serial
        assert ei.value.code == "adb.device_not_found"
        assert "emulator-5554" in str(ei.value)

    def test_explicit_serial_validated_once(self, monkeypatch):
        dev = resolving_device(
            monkeypatch,
            "List of devices attached\nemulator-5554\tdevice\n",
            serial="emulator-5554",
        )
        assert dev.serial == "emulator-5554"
        assert dev.adb_command == ["/fake/adb", "-s", "emulator-5554"]


# ---------------------------------------------------------------------------
# adapter: detection, screenshot, pointer, keys
# ---------------------------------------------------------------------------


def tiny_png(width=4, height=8) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buf, format="PNG")
    return buf.getvalue()


class TestAdbAdapter:
    def test_accepts_only_adbdevice(self, monkeypatch):
        dev, _ = scripted_device(monkeypatch)
        assert AdbAdapter.accepts(dev)
        assert not AdbAdapter.accepts(object())
        assert not AdbAdapter.accepts("emulator-5554")

    def test_screenshot_png_passthrough(self, monkeypatch):
        png = tiny_png(4, 8)
        dev, calls = scripted_device(monkeypatch, {"exec-out screencap -p": png})
        adapter = AdbAdapter(dev)
        from qirabot.adapters.base import ScreenshotConfig

        out = adapter.screenshot(ScreenshotConfig(format="png"))
        assert out == png  # bytes must pass through untouched
        info = adapter.device_info()
        assert (info.width, info.height) == (4, 8)
        assert info.platform == "android"

    def test_screenshot_jpeg_transcodes(self, monkeypatch):
        dev, _ = scripted_device(monkeypatch, {"exec-out screencap -p": tiny_png()})
        adapter = AdbAdapter(dev)
        from qirabot.adapters.base import ScreenshotConfig

        out = adapter.screenshot(ScreenshotConfig(format="jpeg"))
        assert out[:3] == b"\xff\xd8\xff"  # JPEG SOI

    def test_screenshot_empty_retries_then_raises(self, monkeypatch):
        dev, _ = scripted_device(monkeypatch)
        attempts = []
        monkeypatch.setattr(dev, "screencap", lambda: attempts.append(1) or b"")
        monkeypatch.setattr("time.sleep", lambda s: None)
        adapter = AdbAdapter(dev)
        with pytest.raises(QirabotError) as ei:
            adapter.screenshot()
        assert ei.value.code == "adb.screencap_empty"
        assert len(attempts) == 3

    def test_screenshot_truncated_retries_then_succeeds(self, monkeypatch):
        # exec-out cut mid-transfer hands back a half PNG with exit code 0;
        # the adapter must treat it like an empty frame and recapture.
        png = tiny_png(4, 8)
        frames = [png[: len(png) // 2], png]
        dev, _ = scripted_device(monkeypatch)
        monkeypatch.setattr(dev, "screencap", lambda: frames.pop(0))
        monkeypatch.setattr("time.sleep", lambda s: None)
        adapter = AdbAdapter(dev)
        from qirabot.adapters.base import ScreenshotConfig

        out = adapter.screenshot(ScreenshotConfig(format="png"))
        assert out == png

    def test_screenshot_truncated_retries_then_raises(self, monkeypatch):
        png = tiny_png(4, 8)
        attempts = []
        dev, _ = scripted_device(monkeypatch)
        monkeypatch.setattr(dev, "screencap", lambda: attempts.append(1) or png[: len(png) // 2])
        monkeypatch.setattr("time.sleep", lambda s: None)
        adapter = AdbAdapter(dev)
        with pytest.raises(QirabotError) as ei:
            adapter.screenshot()
        assert ei.value.code == "adb.screencap_empty"
        assert "truncated" in str(ei.value)
        assert len(attempts) == 3

    def test_device_info_falls_back_to_wm_size_override(self, monkeypatch):
        dev, _ = scripted_device(
            monkeypatch,
            {"shell wm size": b"Physical size: 1080x2400\nOverride size: 1080x2340\n"},
        )
        info = AdbAdapter(dev).device_info()
        assert (info.width, info.height) == (1080, 2340)  # Override wins

    def test_click_and_double_click(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch)
        adapter = AdbAdapter(dev)
        adapter.click(10.6, 20.2)
        adapter.double_click(30, 40)
        cmds = shell_calls(calls)
        assert cmds[0] == "input tap 10 20"
        assert cmds[1] == "input tap 30 40 && input tap 30 40"  # one round-trip

    def test_long_press_and_drag(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch)
        adapter = AdbAdapter(dev)
        adapter.long_press(5, 6, duration=1.5)
        adapter.drag(0, 0, 100, 200)
        cmds = shell_calls(calls)
        assert cmds[0] == "input swipe 5 6 5 6 1500"
        assert cmds[1] == "input swipe 0 0 100 200 500"

    def test_press_key_mapping(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch)
        adapter = AdbAdapter(dev)
        adapter.press_key("Enter")
        adapter.press_key("Backspace")
        adapter.press_key("ctrl+a")  # combos degrade to the base key
        adapter.press_key("volume_up")  # unknown passes through uppercased
        adapter.go_back()
        cmds = shell_calls(calls)
        assert cmds == [
            "input keyevent ENTER",
            "input keyevent KEYCODE_DEL",
            "input keyevent A",
            "input keyevent VOLUME_UP",
            "input keyevent BACK",
        ]

    def test_clear_focused_single_batched_shell(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch)
        AdbAdapter(dev).clear_focused()
        cmds = shell_calls(calls)
        assert cmds[0] == "input keyevent KEYCODE_MOVE_END"
        assert cmds[1].startswith("input keyevent KEYCODE_DEL ")
        assert cmds[1].count("KEYCODE_DEL") == 64
        assert len(cmds) == 2  # exactly one batched round-trip for the deletes


# ---------------------------------------------------------------------------
# scroll geometry (mirrors the retired airtest adapter's numbers)
# ---------------------------------------------------------------------------


class TestScrollGeometry:
    def make(self, monkeypatch, w=1000, h=2000):
        dev, calls = scripted_device(monkeypatch)
        adapter = AdbAdapter(dev)
        adapter._last_size = (w, h)
        return adapter, calls

    def swipes(self, calls):
        out = []
        for cmd in shell_calls(calls):
            parts = cmd.split()
            assert parts[:2] == ["input", "swipe"]
            out.append(tuple(int(p) for p in parts[2:6]))
        return out

    def test_scroll_amount_anchored_center(self, monkeypatch):
        adapter, calls = self.make(monkeypatch)
        adapter._scroll_action("scroll", {"direction": "down", "amount": 500})
        # center (500, 1000), down = finger moves up by 500
        assert self.swipes(calls) == [(500, 1000, 500, 500)]

    def test_scroll_at_uses_element_anchor(self, monkeypatch):
        adapter, calls = self.make(monkeypatch)
        adapter._scroll_action("scroll_at", {"direction": "up", "amount": 300, "x": 200, "y": 400})
        assert self.swipes(calls) == [(200, 400, 200, 700)]

    def test_scroll_zero_amount_defaults_to_60pct_span(self, monkeypatch):
        adapter, calls = self.make(monkeypatch)
        adapter._scroll_action("scroll", {"direction": "down"})
        # 0.6 * 2000 = 1200; 1000 - 1200 clamped to 0.05 * 2000 = 100
        assert self.swipes(calls) == [(500, 1000, 500, 100)]

    def test_scroll_capped_at_70pct_span(self, monkeypatch):
        adapter, calls = self.make(monkeypatch)
        adapter._scroll_action("scroll", {"direction": "up", "amount": 99999})
        # cap 0.7*2000=1400; 1000+1400=2400 -> clamp to 0.95*2000=1900
        assert self.swipes(calls) == [(500, 1000, 500, 1900)]

    def test_horizontal_uses_width_span(self, monkeypatch):
        adapter, calls = self.make(monkeypatch)
        adapter._scroll_action("scroll", {"direction": "left", "amount": 400})
        assert self.swipes(calls) == [(500, 1000, 900, 1000)]

    def test_legacy_scroll_units(self, monkeypatch):
        adapter, calls = self.make(monkeypatch)
        adapter.scroll(0, 0, "down", 3)  # legacy distance -> x100 px, center anchor
        assert self.swipes(calls) == [(500, 1000, 500, 700)]


# ---------------------------------------------------------------------------
# text input: input text vs IME
# ---------------------------------------------------------------------------


class TestTypeText:
    def test_ascii_typeable_gate(self):
        assert _ascii_typeable("hello world")
        assert _ascii_typeable('a&b;c"d$e`f')
        assert not _ascii_typeable("100%")  # % routes to IME
        assert not _ascii_typeable("你好")
        assert not _ascii_typeable("line\nbreak")
        assert not _ascii_typeable("tab\there")

    def test_input_text_escapes_spaces_and_quotes(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch)
        AdbAdapter(dev).type_focused("it's a test & more")
        cmds = shell_calls(calls)
        assert cmds == ["input text 'it'\\''s%sa%stest%s&%smore'"]

    def test_input_text_chunks_long_strings(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch)
        AdbAdapter(dev).type_focused("a" * 750)
        cmds = shell_calls(calls)
        assert len(cmds) == 3  # 300 + 300 + 150
        assert cmds[0] == "input text '" + "a" * 300 + "'"
        assert cmds[2] == "input text '" + "a" * 150 + "'"

    def test_type_text_taps_then_types(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch)
        monkeypatch.setattr("time.sleep", lambda s: None)
        AdbAdapter(dev).type_text(50, 60, "hi")
        cmds = shell_calls(calls)
        assert cmds[0] == "input tap 50 60"
        assert cmds[1] == "input text 'hi'"

    def test_empty_text_is_noop(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch)
        AdbAdapter(dev).type_focused("")
        assert shell_calls(calls) == []


class TestImePath:
    IME = "com.android.adbkeyboard/.AdbIME"

    def test_ime_flow_with_keyboard_already_installed(self, monkeypatch):
        dev, calls = scripted_device(
            monkeypatch,
            {
                "shell ime list -s -a": self.IME.encode(),
                "shell settings get secure default_input_method": b"com.google.android.inputmethod.latin/.LatinIME\n",
            },
        )
        monkeypatch.setattr("time.sleep", lambda s: None)
        adapter = AdbAdapter(dev)
        adapter.type_focused("你好, world")

        cmds = shell_calls(calls)
        payload = base64.b64encode("你好, world".encode()).decode()
        assert f"ime enable {self.IME}" in cmds
        assert f"ime set {self.IME}" in cmds
        assert f"am broadcast -a ADB_INPUT_B64 --es msg {payload}" in cmds

        # close() restores the saved keyboard
        adapter.close()
        assert shell_calls(calls)[-1] == ("ime set com.google.android.inputmethod.latin/.LatinIME")

    def test_ime_setup_runs_once(self, monkeypatch):
        dev, calls = scripted_device(monkeypatch, {"shell ime list -s -a": self.IME.encode()})
        monkeypatch.setattr("time.sleep", lambda s: None)
        adapter = AdbAdapter(dev)
        adapter.type_focused("第一")
        adapter.type_focused("第二")
        cmds = shell_calls(calls)
        assert cmds.count(f"ime set {self.IME}") == 1
        assert sum(1 for c in cmds if c.startswith("am broadcast")) == 2

    @contextmanager
    def _fake_apk(self, monkeypatch, exists: bool, tmp_path):
        """Point importlib.resources at a fake bundled APK."""
        import importlib.resources as res

        apk_file = tmp_path / "ADBKeyboard.apk"
        if exists:
            apk_file.write_bytes(b"PK\x03\x04fake")

        class FakeTraversable:
            def is_file(self):
                return exists

        class FakeDir:
            def joinpath(self, name):
                assert name == "ADBKeyboard.apk"
                return FakeTraversable()

        @contextmanager
        def fake_as_file(traversable):
            yield apk_file

        monkeypatch.setattr(res, "files", lambda pkg: FakeDir())
        monkeypatch.setattr(res, "as_file", fake_as_file)
        yield apk_file

    def test_missing_ime_installs_bundled_apk(self, monkeypatch, tmp_path):
        dev, calls = scripted_device(monkeypatch, {"shell ime list -s -a": b""})
        installed = []
        monkeypatch.setattr(dev, "install", lambda p: installed.append(p))
        monkeypatch.setattr("time.sleep", lambda s: None)
        with self._fake_apk(monkeypatch, True, tmp_path) as apk_file:
            AdbAdapter(dev).type_focused("汉字")
        assert installed == [str(apk_file)]

    def test_apk_not_bundled_raises_actionable(self, monkeypatch, tmp_path):
        dev, _ = scripted_device(monkeypatch, {"shell ime list -s -a": b""})
        with self._fake_apk(monkeypatch, False, tmp_path):
            with pytest.raises(QirabotError) as ei:
                AdbAdapter(dev).type_focused("汉字")
        assert ei.value.code == "adb.ime_missing"
        assert "appium" in str(ei.value).lower()

    def test_install_blocked_raises_actionable(self, monkeypatch, tmp_path):
        dev, _ = scripted_device(monkeypatch, {"shell ime list -s -a": b""})

        def blocked(path):
            raise QirabotError("INSTALL_FAILED_USER_RESTRICTED", code="adb.command_failed")

        monkeypatch.setattr(dev, "install", blocked)
        with self._fake_apk(monkeypatch, True, tmp_path):
            with pytest.raises(QirabotError) as ei:
                AdbAdapter(dev).type_focused("汉字")
        assert ei.value.code == "adb.ime_install_failed"
        assert "MDM" in str(ei.value)
