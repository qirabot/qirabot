"""Tests for the ffmpeg screen recorder and its client integration."""

import base64
import os
from pathlib import Path

import qirabot.client as client_mod
from qirabot import recording
from qirabot import report as report_mod
from qirabot.client import Qirabot
from qirabot.recording import (
    AdbScreenRecorder,
    AppiumScreenRecorder,
    MjpegStreamRecorder,
    ScreenRecorder,
    _build_input_args,
    _detect_audio_device,
    _detect_screen_index,
    check_mjpeg_stream,
    device_recorder,
)

# Minimal step log entry so write_html / report() has something to render.
_LOG_ENTRY = {
    "section": "setup", "action_type": "click", "params": {}, "output": "",
    "finished": False, "success": True, "coords": None, "screenshot": "", "thumb": "",
}


# --------------------------------------------------------------------------- #
# _build_input_args (per-platform, pure)
# --------------------------------------------------------------------------- #
class TestBuildInputArgs:
    def test_macos(self):
        args = _build_input_args("darwin", 12, True, screen_index="1")
        assert args is not None
        assert args[:2] == ["-f", "avfoundation"]
        assert "-capture_cursor" in args and args[args.index("-capture_cursor") + 1] == "1"
        assert args[-2:] == ["-i", "1:none"]
        assert "-framerate" in args and args[args.index("-framerate") + 1] == "12"

    def test_macos_no_cursor(self):
        args = _build_input_args("darwin", 12, False, screen_index="2")
        assert args is not None
        assert args[args.index("-capture_cursor") + 1] == "0"
        assert args[-1] == "2:none"

    def test_windows(self):
        args = _build_input_args("win32", 30, True)
        assert args is not None
        assert args[:2] == ["-f", "gdigrab"]
        assert args[-2:] == ["-i", "desktop"]
        assert "-draw_mouse" not in args  # cursor on by default, no flag needed

    def test_windows_no_cursor(self):
        args = _build_input_args("win32", 30, False)
        assert args is not None
        assert args[args.index("-draw_mouse") + 1] == "0"

    def test_windows_window_by_title(self):
        args = _build_input_args("win32", 30, True, window="Notepad")
        assert args is not None
        assert args[-2:] == ["-i", "title=Notepad"]

    def test_windows_window_by_handle(self):
        # A numeric window string is treated as an hwnd= handle.
        args = _build_input_args("win32", 30, True, window="13579")
        assert args is not None
        assert args[-2:] == ["-i", "hwnd=13579"]

    def test_windows_region_crops_desktop(self):
        # region grabs the desktop and crops via offset/video_size.
        args = _build_input_args("win32", 30, True, region=(100, 200, 640, 480))
        assert args is not None
        assert args[-2:] == ["-i", "desktop"]
        assert args[args.index("-offset_x") + 1] == "100"
        assert args[args.index("-offset_y") + 1] == "200"
        assert args[args.index("-video_size") + 1] == "640x480"

    def test_windows_region_beats_window(self):
        # When both are given, region (crop) wins over per-window capture.
        args = _build_input_args("win32", 30, True, window="13579", region=(0, 0, 800, 600))
        assert args is not None
        assert args[-1] == "desktop"
        assert "hwnd=13579" not in args

    def test_non_windows_ignores_region(self):
        args = _build_input_args("linux", 15, True, region=(0, 0, 800, 600))
        assert args is not None
        assert "-offset_x" not in args and "-video_size" not in args

    def test_windows_system_audio_second_input(self):
        args = _build_input_args(
            "win32", 30, True, audio_device="virtual-audio-capturer", audio_offset=-0.4
        )
        assert args is not None
        # Video input first (desktop), then a dshow audio input.
        assert "desktop" in args
        assert "dshow" in args
        assert "audio=virtual-audio-capturer" in args
        assert "-rtbufsize" in args
        assert args[args.index("-itsoffset") + 1] == "-0.4"

    def test_windows_no_audio_no_dshow(self):
        args = _build_input_args("win32", 30, True)
        assert args is not None
        assert "dshow" not in args  # silent unless an audio device is given

    def test_non_windows_ignores_window_and_audio(self):
        # window/audio are Windows-only; other platforms keep full-screen silent.
        args = _build_input_args("linux", 15, True, window="X", audio_device="Y")
        assert args is not None
        assert "dshow" not in args and "title=X" not in args

    def test_linux_uses_display_env(self, monkeypatch):
        monkeypatch.setenv("DISPLAY", ":5")
        args = _build_input_args("linux", 15, True)
        assert args is not None
        assert args[:2] == ["-f", "x11grab"]
        assert args[-2:] == ["-i", ":5"]

    def test_linux_default_display(self, monkeypatch):
        monkeypatch.delenv("DISPLAY", raising=False)
        args = _build_input_args("linux", 15, True)
        assert args is not None
        assert args[-1] == ":0.0"

    def test_unsupported_platform(self):
        assert _build_input_args("sunos5", 12, True) is None


# --------------------------------------------------------------------------- #
# _detect_screen_index (macOS avfoundation probe)
# --------------------------------------------------------------------------- #
class _FakeCompleted:
    def __init__(self, stderr):
        self.stderr = stderr


class TestDetectScreenIndex:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("QIRA_SCREEN_INDEX", "7")
        assert _detect_screen_index("ffmpeg") == "7"

    def test_parses_capture_screen_line(self, monkeypatch):
        monkeypatch.delenv("QIRA_SCREEN_INDEX", raising=False)
        stderr = (
            "[AVFoundation] AVFoundation video devices:\n"
            "[AVFoundation] [0] FaceTime HD Camera\n"
            "[AVFoundation] [3] Capture screen 0\n"
        )
        monkeypatch.setattr(recording.subprocess, "run", lambda *a, **k: _FakeCompleted(stderr))
        assert _detect_screen_index("ffmpeg") == "3"

    def test_falls_back_on_error(self, monkeypatch):
        monkeypatch.delenv("QIRA_SCREEN_INDEX", raising=False)

        def boom(*a, **k):
            raise OSError("no ffmpeg")

        monkeypatch.setattr(recording.subprocess, "run", boom)
        assert _detect_screen_index("ffmpeg") == "1"


# --------------------------------------------------------------------------- #
# _detect_audio_device (Windows dshow system-audio probe)
# --------------------------------------------------------------------------- #
class TestDetectAudioDevice:
    def test_env_override_wins(self, monkeypatch):
        monkeypatch.setenv("QIRA_AUDIO_DEVICE", "CABLE Output")
        assert _detect_audio_device("ffmpeg") == "CABLE Output"

    def test_prefers_virtual_audio_capturer(self, monkeypatch):
        monkeypatch.delenv("QIRA_AUDIO_DEVICE", raising=False)
        stderr = (
            '[dshow] "Microphone (Realtek Audio)" (audio)\n'
            '[dshow] "Stereo Mix (Realtek Audio)" (audio)\n'
            '[dshow] "virtual-audio-capturer" (audio)\n'
        )
        monkeypatch.setattr(recording.subprocess, "run", lambda *a, **k: _FakeCompleted(stderr))
        assert _detect_audio_device("ffmpeg") == "virtual-audio-capturer"

    def test_falls_back_to_stereo_mix(self, monkeypatch):
        monkeypatch.delenv("QIRA_AUDIO_DEVICE", raising=False)
        stderr = (
            '[dshow] "Microphone (Realtek Audio)" (audio)\n'
            '[dshow] "Stereo Mix (Realtek Audio)" (audio)\n'
        )
        monkeypatch.setattr(recording.subprocess, "run", lambda *a, **k: _FakeCompleted(stderr))
        assert _detect_audio_device("ffmpeg") == "Stereo Mix (Realtek Audio)"

    def test_none_when_no_match(self, monkeypatch):
        monkeypatch.delenv("QIRA_AUDIO_DEVICE", raising=False)
        stderr = '[dshow] "Microphone (Realtek Audio)" (audio)\n'
        monkeypatch.setattr(recording.subprocess, "run", lambda *a, **k: _FakeCompleted(stderr))
        assert _detect_audio_device("ffmpeg") is None

    def test_none_on_error(self, monkeypatch):
        monkeypatch.delenv("QIRA_AUDIO_DEVICE", raising=False)

        def boom(*a, **k):
            raise OSError("no ffmpeg")

        monkeypatch.setattr(recording.subprocess, "run", boom)
        assert _detect_audio_device("ffmpeg") is None

    def test_decodes_as_utf8_with_replacement(self, monkeypatch):
        # On a non-UTF-8 console a locale (e.g. GBK) decode crashes the stdio
        # reader thread; the probe must force UTF-8 + replace, not the default.
        monkeypatch.delenv("QIRA_AUDIO_DEVICE", raising=False)
        seen = {}

        def fake_run(*a, **k):
            seen.update(k)
            return _FakeCompleted('"virtual-audio-capturer" (audio)\n')

        monkeypatch.setattr(recording.subprocess, "run", fake_run)
        assert _detect_audio_device("ffmpeg") == "virtual-audio-capturer"
        assert seen.get("encoding") == "utf-8"
        assert seen.get("errors") == "replace"

    def test_none_stderr_does_not_crash(self, monkeypatch):
        # A crashed reader thread leaves stderr=None; the probe must degrade to
        # None instead of raising TypeError from re.findall(None).
        monkeypatch.delenv("QIRA_AUDIO_DEVICE", raising=False)
        monkeypatch.setattr(recording.subprocess, "run", lambda *a, **k: _FakeCompleted(None))
        assert _detect_audio_device("ffmpeg") is None


# --------------------------------------------------------------------------- #
# ScreenRecorder.start / stop
# --------------------------------------------------------------------------- #
class _FakeStdin:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, b):
        self.data += b

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakeProc:
    def __init__(self):
        self.stdin = _FakeStdin()
        self._poll = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self._poll

    def wait(self, timeout=None):
        self._poll = 0
        return 0

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


class TestScreenRecorder:
    def test_start_degrades_without_ffmpeg(self, monkeypatch, tmp_path):
        monkeypatch.setattr(recording.shutil, "which", lambda _: None)
        rec = ScreenRecorder(str(tmp_path / "recording.mp4"))
        assert rec.start() is False  # warns, never raises
        assert rec.active is False

    def test_start_builds_ffmpeg_command(self, monkeypatch, tmp_path):
        captured = {}

        class _FakePopen:
            def __init__(self, cmd, **kw):
                captured["cmd"] = cmd
                captured["kw"] = kw
                self.stdin = _FakeStdin()

            def poll(self):
                return None

        monkeypatch.setattr(recording.sys, "platform", "darwin")
        monkeypatch.setattr(recording, "_find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(recording, "_detect_screen_index", lambda f: "1")
        monkeypatch.setattr(recording.subprocess, "Popen", _FakePopen)

        out = str(tmp_path / "recording.mp4")
        rec = ScreenRecorder(out, fps=20)
        assert rec.start() is True
        assert rec.active is True

        cmd = captured["cmd"]
        assert cmd[0] == "ffmpeg" and cmd[1] == "-y"
        assert cmd[-1] == out
        for token in ("-f", "avfoundation", "libx264", "ultrafast", "yuv420p"):
            assert token in cmd
        assert captured["kw"]["stdin"] is recording.subprocess.PIPE

    def test_start_windows_window_and_audio_command(self, monkeypatch, tmp_path):
        captured = {}

        class _FakePopen:
            def __init__(self, cmd, **kw):
                captured["cmd"] = cmd
                self.stdin = _FakeStdin()

            def poll(self):
                return None

        monkeypatch.setattr(recording.sys, "platform", "win32")
        monkeypatch.setattr(recording, "_find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(recording, "_detect_audio_device", lambda f: "virtual-audio-capturer")
        monkeypatch.setattr(recording.subprocess, "Popen", _FakePopen)

        out = str(tmp_path / "recording.mp4")
        rec = ScreenRecorder(out, fps=15, window="Notepad", audio=True)
        assert rec.start() is True

        cmd = captured["cmd"]
        assert "title=Notepad" in cmd               # per-window video input
        assert "audio=virtual-audio-capturer" in cmd  # system-audio input
        assert cmd[cmd.index("-c:a") + 1] == "aac"  # audio gets encoded
        assert cmd[-1] == out

    def test_start_windows_audio_missing_device_is_silent(self, monkeypatch, tmp_path):
        captured = {}

        class _FakePopen:
            def __init__(self, cmd, **kw):
                captured["cmd"] = cmd
                self.stdin = _FakeStdin()

            def poll(self):
                return None

        monkeypatch.setattr(recording.sys, "platform", "win32")
        monkeypatch.setattr(recording, "_find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(recording, "_detect_audio_device", lambda f: None)  # none found
        monkeypatch.setattr(recording.subprocess, "Popen", _FakePopen)

        rec = ScreenRecorder(str(tmp_path / "recording.mp4"), audio=True)
        assert rec.start() is True  # degrades to silent, never fails
        cmd = captured["cmd"]
        assert "-c:a" not in cmd and "dshow" not in cmd

    def test_stop_sends_q_and_returns_path(self, monkeypatch, tmp_path):
        out = tmp_path / "recording.mp4"
        out.write_bytes(b"fake-mp4-bytes")  # non-empty -> treated as valid
        rec = ScreenRecorder(str(out))
        proc = _FakeProc()
        rec._proc = proc

        assert rec.stop() == str(out)
        assert proc.stdin.data == b"q\n"  # graceful quit signal sent
        assert proc.stdin.closed is True

    def test_stop_without_proc_is_noop(self, tmp_path):
        rec = ScreenRecorder(str(tmp_path / "recording.mp4"))
        assert rec.stop() is None

    def test_stop_no_file_returns_none(self, tmp_path):
        rec = ScreenRecorder(str(tmp_path / "missing.mp4"))
        rec._proc = _FakeProc()
        assert rec.stop() is None  # nothing was written


# --------------------------------------------------------------------------- #
# MjpegStreamRecorder (WDA device-screen stream)
# --------------------------------------------------------------------------- #
class TestMjpegStreamRecorder:
    def test_start_builds_stream_command(self, monkeypatch, tmp_path):
        captured = {}

        class _FakePopen:
            def __init__(self, cmd, **kw):
                captured["cmd"] = cmd
                self.stdin = _FakeStdin()

            def poll(self):
                return None

        monkeypatch.setattr(recording, "_find_ffmpeg", lambda: "ffmpeg")
        monkeypatch.setattr(recording.subprocess, "Popen", _FakePopen)

        out = str(tmp_path / "recording.mp4")
        rec = MjpegStreamRecorder(out, "http://127.0.0.1:9100")
        assert rec.start() is True
        assert rec.active is True

        cmd = captured["cmd"]
        assert cmd[0] == "ffmpeg" and cmd[1] == "-y"
        assert cmd[cmd.index("-f") + 1] == "mjpeg"
        assert cmd[cmd.index("-i") + 1] == "http://127.0.0.1:9100"
        # The stream has no timestamps: wallclock keeps playback real-time,
        # and it must be an *input* option (before -i).
        assert cmd.index("-use_wallclock_as_timestamps") < cmd.index("-i")
        # Odd device dimensions are scaled to even ones for yuv420p.
        assert "scale=trunc(iw/2)*2:trunc(ih/2)*2" in cmd
        for token in ("libx264", "ultrafast", "yuv420p"):
            assert token in cmd
        assert cmd[-1] == out
        # No host-screen grabbers leak into the stream command.
        for grabber in ("avfoundation", "gdigrab", "x11grab", "dshow"):
            assert grabber not in cmd

    def test_start_degrades_without_ffmpeg(self, monkeypatch, tmp_path):
        monkeypatch.setattr(recording.shutil, "which", lambda _: None)
        rec = MjpegStreamRecorder(str(tmp_path / "recording.mp4"), "http://h:9100")
        assert rec.start() is False
        assert rec.active is False

    def test_stop_sends_q_and_returns_path(self, tmp_path):
        # Inherited stop lifecycle works unchanged for the stream recorder.
        out = tmp_path / "recording.mp4"
        out.write_bytes(b"fake-mp4-bytes")
        rec = MjpegStreamRecorder(str(out), "http://h:9100")
        proc = _FakeProc()
        rec._proc = proc
        assert rec.stop() == str(out)
        assert proc.stdin.data == b"q\n"


# --------------------------------------------------------------------------- #
# check_mjpeg_stream (fail-fast probe)
# --------------------------------------------------------------------------- #
class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self, n):
        return self._data[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestCheckMjpegStream:
    def test_ok_when_stream_sends_data(self, monkeypatch):
        monkeypatch.setattr(
            recording.urllib.request, "urlopen",
            lambda url, timeout=None: _FakeResponse(b"\xff\xd8"),
        )
        assert check_mjpeg_stream("http://127.0.0.1:9100") is None

    def test_connected_but_silent_is_an_error(self, monkeypatch):
        # Something answered the port but streams nothing (wrong service).
        monkeypatch.setattr(
            recording.urllib.request, "urlopen",
            lambda url, timeout=None: _FakeResponse(b""),
        )
        err = check_mjpeg_stream("http://127.0.0.1:9100")
        assert err is not None and "no data" in err

    def test_connection_refused_is_an_error(self, monkeypatch):
        def refuse(url, timeout=None):
            raise OSError("Connection refused")

        monkeypatch.setattr(recording.urllib.request, "urlopen", refuse)
        err = check_mjpeg_stream("http://127.0.0.1:9100")
        assert err is not None
        assert "http://127.0.0.1:9100" in err and "Connection refused" in err


# --------------------------------------------------------------------------- #
# AppiumScreenRecorder (device screen via the Appium session API)
# --------------------------------------------------------------------------- #
class _FakeAppiumDriver:
    def __init__(self, platform="android", reject_options=False, payload=b"video-bytes"):
        self.capabilities = {"platformName": platform}
        self.reject_options = reject_options
        self.payload = payload
        self.start_calls = []
        self.stopped = False

    def start_recording_screen(self, **opts):
        if self.reject_options and opts:
            raise ValueError("unsupported options")
        self.start_calls.append(opts)

    def stop_recording_screen(self):
        self.stopped = True
        return base64.b64encode(self.payload).decode()


class TestAppiumScreenRecorder:
    def test_start_raises_time_limit_and_stop_writes_file(self, tmp_path):
        driver = _FakeAppiumDriver()
        out = str(tmp_path / "recording.mp4")
        rec = AppiumScreenRecorder(out, driver)

        assert rec.start() is True
        assert rec.active is True
        opts = driver.start_calls[0]
        # Default 180s timeLimit silently truncates long runs — must be raised.
        assert opts["timeLimit"] == 1800
        assert "videoType" not in opts  # android keeps the driver default

        assert rec.stop() == out
        assert driver.stopped is True
        with open(out, "rb") as f:
            assert f.read() == b"video-bytes"

    def test_ios_pins_playable_codec(self, tmp_path):
        # XCUITest's default codec is mjpeg, which HTML5 <video> can't play.
        driver = _FakeAppiumDriver(platform="iOS")
        rec = AppiumScreenRecorder(str(tmp_path / "recording.mp4"), driver)
        assert rec.start() is True
        assert driver.start_calls[0]["videoType"] == "libx264"

    def test_old_driver_rejecting_options_gets_bare_start(self, tmp_path):
        driver = _FakeAppiumDriver(reject_options=True)
        rec = AppiumScreenRecorder(str(tmp_path / "recording.mp4"), driver)
        assert rec.start() is True
        assert driver.start_calls == [{}]  # retried without options

    def test_start_failure_degrades(self, tmp_path):
        class _Broken:
            def start_recording_screen(self, **opts):
                raise RuntimeError("no session")

        rec = AppiumScreenRecorder(str(tmp_path / "recording.mp4"), _Broken())
        assert rec.start() is False
        assert rec.active is False

    def test_stop_empty_payload_returns_none(self, tmp_path):
        driver = _FakeAppiumDriver(payload=b"")
        rec = AppiumScreenRecorder(str(tmp_path / "recording.mp4"), driver)
        rec.start()
        assert rec.stop() is None

    def test_stop_without_start_is_noop(self, tmp_path):
        rec = AppiumScreenRecorder(str(tmp_path / "recording.mp4"), _FakeAppiumDriver())
        assert rec.stop() is None


# --------------------------------------------------------------------------- #
# AdbScreenRecorder (adb shell screenrecord, segmented)
# --------------------------------------------------------------------------- #
class TestAdbScreenRecorder:
    def _fake_pull(self, tmp_path, pulled, fail_pull=False):
        """subprocess.run stub: records commands; 'pull' writes a local file."""
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if "pull" in cmd and not fail_pull:
                local = cmd[cmd.index("pull") + 2]
                with open(local, "wb") as f:
                    f.write(b"segment-bytes")
                pulled.append(local)
            return _FakeCompleted("")

        return calls, fake_run

    def test_record_loop_builds_screenrecord_command(self, monkeypatch, tmp_path):
        spawned = []

        class _LoopPopen:
            def __init__(self_inner, cmd, **kw):
                spawned.append(cmd)
                self_inner.returncode = 0

            def wait(self_inner):
                # Simulate stop() arriving mid-segment: screenrecord got
                # SIGINT, exits 0, and the loop must not respawn.
                rec._stop.set()
                return 0

            def poll(self_inner):
                return 0

        monkeypatch.setattr(recording.subprocess, "Popen", _LoopPopen)
        rec = AdbScreenRecorder(str(tmp_path / "recording.mp4"), ["adb", "-s", "emu-1"])
        rec._record_loop()

        assert len(spawned) == 1
        cmd = spawned[0]
        assert cmd[:3] == ["adb", "-s", "emu-1"]
        assert "shell" in cmd and "screenrecord" in cmd
        assert cmd[cmd.index("--time-limit") + 1] == "180"
        assert cmd[-1].startswith("/sdcard/qira_rec_") and cmd[-1].endswith(".mp4")

    def test_record_loop_bails_on_instant_failure(self, monkeypatch, tmp_path):
        # screenrecord dying immediately (no binary/permission) must not be
        # respawned in a tight loop until stop().
        spawned = []

        class _DeadPopen:
            def __init__(self_inner, cmd, **kw):
                spawned.append(cmd)
                self_inner.returncode = 1

            def wait(self_inner):
                return 1

            def poll(self_inner):
                return 1

        monkeypatch.setattr(recording.subprocess, "Popen", _DeadPopen)
        rec = AdbScreenRecorder(str(tmp_path / "recording.mp4"), ["adb"])
        rec._record_loop()
        assert len(spawned) == 1

    def test_stop_signals_pulls_and_renames_single_segment(self, monkeypatch, tmp_path):
        out = str(tmp_path / "recording.mp4")
        pulled = []
        calls, fake_run = self._fake_pull(tmp_path, pulled)
        monkeypatch.setattr(recording.subprocess, "run", fake_run)

        rec = AdbScreenRecorder(out, ["adb", "-s", "emu-1"])
        rec._started = True
        rec._remote = ["/sdcard/qira_rec_1_000.mp4"]

        assert rec.stop() == out
        # SIGINT went to the device so screenrecord finalizes the moov atom.
        assert any("pkill" in c and "-2" in c for c in calls)
        # Remote segment pulled then removed.
        assert any("pull" in c for c in calls)
        assert any("rm" in c for c in calls)
        assert os.path.getsize(out) > 0
        assert rec.active is False

    def test_stop_merges_segments_with_ffmpeg(self, monkeypatch, tmp_path):
        out = str(tmp_path / "recording.mp4")
        pulled = []
        calls, fake_run = self._fake_pull(tmp_path, pulled)

        def fake_run_with_concat(cmd, **kw):
            if cmd[0] == "ffmpeg":
                calls.append(cmd)
                with open(cmd[-1], "wb") as f:
                    f.write(b"merged")
                return _FakeCompleted("")
            return fake_run(cmd, **kw)

        monkeypatch.setattr(recording.subprocess, "run", fake_run_with_concat)
        monkeypatch.setattr(recording, "_find_ffmpeg", lambda: "ffmpeg")

        rec = AdbScreenRecorder(out, ["adb"])
        rec._started = True
        rec._remote = ["/sdcard/a.mp4", "/sdcard/b.mp4"]

        assert rec.stop() == out
        concat = next(c for c in calls if c and c[0] == "ffmpeg")
        assert "concat" in concat and concat[-1] == out
        with open(out, "rb") as f:
            assert f.read() == b"merged"
        # Parts and the concat list are cleaned up.
        assert list(tmp_path.iterdir()) == [tmp_path / "recording.mp4"]

    def test_stop_without_ffmpeg_keeps_first_segment(self, monkeypatch, tmp_path):
        out = str(tmp_path / "recording.mp4")
        pulled = []
        calls, fake_run = self._fake_pull(tmp_path, pulled)
        monkeypatch.setattr(recording.subprocess, "run", fake_run)
        monkeypatch.setattr(recording, "_find_ffmpeg", lambda: None)

        rec = AdbScreenRecorder(out, ["adb"])
        rec._started = True
        rec._remote = ["/sdcard/a.mp4", "/sdcard/b.mp4"]

        assert rec.stop() == out  # first segment becomes the recording
        assert os.path.exists(out)

    def test_stop_with_nothing_pulled_returns_none(self, monkeypatch, tmp_path):
        pulled = []
        calls, fake_run = self._fake_pull(tmp_path, pulled, fail_pull=True)
        monkeypatch.setattr(recording.subprocess, "run", fake_run)

        rec = AdbScreenRecorder(str(tmp_path / "recording.mp4"), ["adb"])
        rec._started = True
        rec._remote = ["/sdcard/a.mp4"]
        assert rec.stop() is None

    def test_stop_without_start_is_noop(self, tmp_path):
        rec = AdbScreenRecorder(str(tmp_path / "recording.mp4"), ["adb"])
        assert rec.stop() is None


# --------------------------------------------------------------------------- #
# device_recorder (target -> recorder selection)
# --------------------------------------------------------------------------- #
class TestDeviceRecorder:
    def test_appium_driver_selected(self, tmp_path):
        rec = device_recorder(str(tmp_path / "r.mp4"), _FakeAppiumDriver())
        assert isinstance(rec, AppiumScreenRecorder)

    def test_adbdevice_selected(self, tmp_path):
        from qirabot.adb import AdbDevice

        dev = AdbDevice(serial="emulator-5554")
        dev._adb_path = "/opt/adb"
        dev._serial_checked = True
        rec = device_recorder(str(tmp_path / "r.mp4"), dev)
        assert isinstance(rec, AdbScreenRecorder)
        assert rec._adb == ["/opt/adb", "-s", "emulator-5554"]

    def test_adbdevice_resolution_failure_returns_none(self, tmp_path):
        from qirabot.adb import AdbDevice
        from qirabot.exceptions import QirabotError

        class _Unresolvable(AdbDevice):
            @property
            def adb_command(self) -> list[str]:
                raise QirabotError("no adb", code="adb.not_found")

        assert device_recorder(str(tmp_path / "r.mp4"), _Unresolvable()) is None

    def test_unsupported_target_returns_none(self, tmp_path):
        assert device_recorder(str(tmp_path / "r.mp4"), object()) is None
        assert device_recorder(str(tmp_path / "r.mp4"), None) is None


# --------------------------------------------------------------------------- #
# Client integration
# --------------------------------------------------------------------------- #
class _FakeRecorder:
    """Drop-in for ScreenRecorder that records lifecycle without spawning ffmpeg."""

    instances: list["_FakeRecorder"] = []

    def __init__(self, output, *, fps=12, capture_cursor=True, window=None, region=None, audio=False, audio_offset=None):
        self.output = output
        self.fps = fps
        self.window = window
        self.region = region
        self.audio = audio
        self.audio_offset = audio_offset
        self.started = False
        self.stopped = False
        self._active = False
        _FakeRecorder.instances.append(self)

    def start(self):
        self.started = True
        self._active = True
        return True

    @property
    def active(self):
        return self._active

    def stop(self, timeout=10.0):
        self.stopped = True
        self._active = False
        return self.output


class TestClientRecordingWiring:
    def _use_fake(self, monkeypatch):
        _FakeRecorder.instances = []
        monkeypatch.setattr(client_mod, "ScreenRecorder", _FakeRecorder)

    def test_record_true_autostarts(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        Qirabot(record=True, report_dir=str(tmp_path))
        assert len(_FakeRecorder.instances) == 1
        rec = _FakeRecorder.instances[0]
        assert rec.started is True
        assert rec.output.endswith("recording.mp4")

    def test_record_false_does_not_record(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        bot = Qirabot(report_dir=str(tmp_path))
        assert _FakeRecorder.instances == []
        assert bot._recorder is None

    def test_env_var_enables_recording(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        monkeypatch.setenv("QIRA_RECORD", "1")
        Qirabot(report_dir=str(tmp_path))
        assert len(_FakeRecorder.instances) == 1

    def test_start_recording_is_idempotent(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        bot = Qirabot(record=True, report_dir=str(tmp_path))
        assert bot.start_recording() is True  # already running -> no new ffmpeg
        assert len(_FakeRecorder.instances) == 1

    def test_stop_recording_clears_slot(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        bot = Qirabot(record=True, report_dir=str(tmp_path))
        rec = _FakeRecorder.instances[0]
        assert bot.stop_recording() == rec.output
        assert rec.stopped is True
        assert bot._recorder is None
        # close() must not try to stop again (slot already cleared)
        bot.close()
        assert len(_FakeRecorder.instances) == 1

    def test_close_stops_before_report_written(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        bot = Qirabot(record=True, report_dir=str(tmp_path))
        rec = _FakeRecorder.instances[0]

        order = {}

        def fake_write_report(out=None):
            order["stopped_when_report_written"] = rec.stopped
            return None

        monkeypatch.setattr(bot, "_write_report", fake_write_report)
        bot.close()
        assert order["stopped_when_report_written"] is True

    def test_manual_recording_without_flag(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        bot = Qirabot(report_dir=str(tmp_path))
        assert bot.start_recording() is True
        assert len(_FakeRecorder.instances) == 1
        assert bot.stop_recording() == _FakeRecorder.instances[0].output

    def test_record_window_defers_until_target(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        bot = Qirabot(
            record=True, record_window=True, report_dir=str(tmp_path)
        )
        # No target at construction time -> deferred, nothing started yet.
        assert _FakeRecorder.instances == []
        assert bot._recorder is None
        # First action resolves a window; the _get_adapter hook then starts it.
        monkeypatch.setattr(bot, "_resolve_window_target", lambda target: "My Window")
        bot._maybe_start_recording(target=object())
        assert len(_FakeRecorder.instances) == 1
        rec = _FakeRecorder.instances[0]
        assert rec.window == "My Window" and rec.started is True

    def test_record_window_degrades_to_fullscreen(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        bot = Qirabot(
            record=True, record_window=True, report_dir=str(tmp_path)
        )
        monkeypatch.setattr(bot, "_resolve_window_target", lambda target: None)  # not resolvable
        bot._maybe_start_recording(target=object())
        assert len(_FakeRecorder.instances) == 1
        assert _FakeRecorder.instances[0].window is None  # full screen fallback

    def test_record_window_starts_only_once(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        bot = Qirabot(
            record=True, record_window=True, report_dir=str(tmp_path)
        )
        monkeypatch.setattr(bot, "_resolve_window_target", lambda target: "W")
        bot._maybe_start_recording(target=object())
        bot._maybe_start_recording(target=object())  # later action -> no second ffmpeg
        assert len(_FakeRecorder.instances) == 1

    def test_record_audio_passed_to_recorder(self, monkeypatch, tmp_path):
        self._use_fake(monkeypatch)
        Qirabot(
            record=True, record_audio=True, report_dir=str(tmp_path)
        )
        # record_window off -> starts immediately (full screen) with audio on.
        assert len(_FakeRecorder.instances) == 1
        assert _FakeRecorder.instances[0].audio is True


class _FakeMjpegRecorder:
    """Drop-in for MjpegStreamRecorder that records lifecycle without ffmpeg."""

    instances: list["_FakeMjpegRecorder"] = []

    def __init__(self, output, url):
        self.output = output
        self.url = url
        self.started = False
        self.stopped = False
        _FakeMjpegRecorder.instances.append(self)

    def start(self):
        self.started = True
        return True

    @property
    def active(self):
        return self.started and not self.stopped

    def stop(self, timeout=10.0):
        self.stopped = True
        return self.output


class TestClientMjpegRecordingWiring:
    def _use_fakes(self, monkeypatch):
        _FakeRecorder.instances = []
        _FakeMjpegRecorder.instances = []
        monkeypatch.setattr(client_mod, "ScreenRecorder", _FakeRecorder)
        monkeypatch.setattr(client_mod, "MjpegStreamRecorder", _FakeMjpegRecorder)

    def test_mjpeg_url_routes_to_stream_recorder(self, monkeypatch, tmp_path):
        self._use_fakes(monkeypatch)
        Qirabot(
            record=True,
            record_mjpeg_url="http://127.0.0.1:9100", report_dir=str(tmp_path),
        )
        assert _FakeRecorder.instances == []  # host-screen path not taken
        assert len(_FakeMjpegRecorder.instances) == 1
        rec = _FakeMjpegRecorder.instances[0]
        assert rec.started is True
        assert rec.url == "http://127.0.0.1:9100"
        assert rec.output.endswith("recording.mp4")

    def test_env_var_sets_mjpeg_url(self, monkeypatch, tmp_path):
        self._use_fakes(monkeypatch)
        monkeypatch.setenv("QIRA_RECORD_MJPEG_URL", "http://10.0.0.5:9100")
        Qirabot(record=True, report_dir=str(tmp_path))
        assert len(_FakeMjpegRecorder.instances) == 1
        assert _FakeMjpegRecorder.instances[0].url == "http://10.0.0.5:9100"

    def test_mjpeg_url_alone_implies_recording(self, monkeypatch, tmp_path):
        # Passing a recording source is the opt-in; record=True is not
        # required on top of it.
        self._use_fakes(monkeypatch)
        Qirabot(
            record_mjpeg_url="http://127.0.0.1:9100", report_dir=str(tmp_path),
        )
        assert _FakeRecorder.instances == []
        assert len(_FakeMjpegRecorder.instances) == 1
        assert _FakeMjpegRecorder.instances[0].started is True

    def test_mjpeg_ignores_record_window_deferral(self, monkeypatch, tmp_path):
        # record_window defers until an action supplies a window target; the
        # device stream has no host window, so it must start immediately.
        self._use_fakes(monkeypatch)
        Qirabot(
            record=True, record_window=True,
            record_mjpeg_url="http://127.0.0.1:9100", report_dir=str(tmp_path),
        )
        assert len(_FakeMjpegRecorder.instances) == 1
        assert _FakeMjpegRecorder.instances[0].started is True

    def test_stop_recording_returns_stream_path(self, monkeypatch, tmp_path):
        self._use_fakes(monkeypatch)
        bot = Qirabot(
            record=True,
            record_mjpeg_url="http://127.0.0.1:9100", report_dir=str(tmp_path),
        )
        rec = _FakeMjpegRecorder.instances[0]
        assert bot.stop_recording() == rec.output
        assert rec.stopped is True


class TestClientDeviceRecordingWiring:
    def _setup(self, monkeypatch, factory_result="fake"):
        """Patch ScreenRecorder (must stay unused) and device_recorder."""
        _FakeRecorder.instances = []
        monkeypatch.setattr(client_mod, "ScreenRecorder", _FakeRecorder)
        made = []

        def fake_device_recorder(output, target):
            if factory_result is None:
                return None
            rec = _FakeRecorder(output)
            rec.target = target
            made.append(rec)
            return rec

        monkeypatch.setattr(client_mod, "device_recorder", fake_device_recorder)
        return made

    def test_defers_until_target_then_records_device(self, monkeypatch, tmp_path):
        made = self._setup(monkeypatch)
        bot = Qirabot(
            record=True, record_device=True,
            report_dir=str(tmp_path),
        )
        # No target at construction -> deferred (an eager start would grab the
        # host screen, which is the wrong thing for a device run).
        assert made == [] and bot._recorder is None

        driver = object()
        bot._maybe_start_recording(target=driver)
        assert len(made) == 1
        assert made[0].started is True
        assert made[0].target is driver
        # Later actions must not spawn a second recorder.
        bot._maybe_start_recording(target=driver)
        assert len(made) == 1

    def test_env_var_enables_device_recording(self, monkeypatch, tmp_path):
        made = self._setup(monkeypatch)
        monkeypatch.setenv("QIRA_RECORD_DEVICE", "1")
        bot = Qirabot(record=True, report_dir=str(tmp_path))
        assert bot._recorder is None  # still deferred
        bot._maybe_start_recording(target=object())
        assert len(made) == 1

    def test_unsupported_target_skips_instead_of_host_screen(self, monkeypatch, tmp_path):
        # Falling back to the host screen would record the desktop the SDK
        # runs on — worse than no recording plus the report notice.
        self._setup(monkeypatch, factory_result=None)
        bot = Qirabot(
            record=True, record_device=True,
            report_dir=str(tmp_path),
        )
        bot._maybe_start_recording(target=object())
        assert bot._recorder is None
        assert _FakeRecorder.instances == []  # host ScreenRecorder untouched

    def test_mjpeg_url_wins_over_record_device(self, monkeypatch, tmp_path):
        # Both set (ios direct passes mjpeg; device flag could come from env):
        # the MJPEG stream starts immediately, no deferral.
        made = self._setup(monkeypatch)
        fake_mjpeg = []
        monkeypatch.setattr(
            client_mod, "MjpegStreamRecorder",
            lambda output, url: fake_mjpeg.append(url) or _FakeRecorder(output),
        )
        Qirabot(
            record=True, record_device=True,
            record_mjpeg_url="http://127.0.0.1:9100", report_dir=str(tmp_path),
        )
        assert fake_mjpeg == ["http://127.0.0.1:9100"]
        assert made == []


# --------------------------------------------------------------------------- #
# Recording-failure notice in the report
# --------------------------------------------------------------------------- #
class _FailingRecorder:
    """Recorder whose start() fails (e.g. ffmpeg missing)."""

    def __init__(self, output, *, fps=12, capture_cursor=True, window=None, region=None, audio=False, audio_offset=None):
        self.output = output

    def start(self):
        return False

    @property
    def active(self):
        return False

    def stop(self, timeout=10.0):
        return None


class TestReportRecordingNotice:
    def test_write_html_renders_notice(self, tmp_path):
        out = report_mod.write_html(
            [_LOG_ENTRY], tmp_path / "r.html", record_error="ffmpeg not found"
        )
        markup = out.read_text(encoding="utf-8")
        assert "class='notice'" in markup
        assert "ffmpeg not found" in markup

    def test_write_html_video_wins_over_error(self, tmp_path):
        out = report_mod.write_html(
            [_LOG_ENTRY], tmp_path / "r.html",
            recording="recording.mp4", record_error="ignored",
        )
        markup = out.read_text(encoding="utf-8")
        assert "<video" in markup
        assert "class='notice'" not in markup

    def test_client_notes_failed_recording(self, monkeypatch, tmp_path):
        monkeypatch.setattr(client_mod, "ScreenRecorder", _FailingRecorder)
        bot = Qirabot(record=True, report_dir=str(tmp_path))
        assert bot._recorder is None  # start() failed
        bot._log.append(dict(_LOG_ENTRY))
        out = bot.report(str(tmp_path / "report.html"))
        markup = Path(out).read_text(encoding="utf-8")
        assert "Recording was requested but not produced" in markup
        assert "<video" not in markup

    def test_client_zero_byte_recording_is_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(client_mod, "ScreenRecorder", _FailingRecorder)
        bot = Qirabot(record=True, report_dir=str(tmp_path))
        (Path(bot.report_dir) / "recording.mp4").write_bytes(b"")  # 0 bytes
        bot._log.append(dict(_LOG_ENTRY))
        out = bot.report(str(tmp_path / "report.html"))
        markup = Path(out).read_text(encoding="utf-8")
        assert "Recording was requested but not produced" in markup
        assert "<video" not in markup

    def test_client_valid_recording_embeds_no_notice(self, monkeypatch, tmp_path):
        monkeypatch.setattr(client_mod, "ScreenRecorder", _FailingRecorder)
        bot = Qirabot(record=True, report_dir=str(tmp_path))
        (Path(bot.report_dir) / "recording.mp4").write_bytes(b"data")
        bot._log.append(dict(_LOG_ENTRY))
        out = bot.report(str(tmp_path / "report.html"))
        markup = Path(out).read_text(encoding="utf-8")
        assert "<video" in markup
        assert "Recording was requested but not produced" not in markup

    def test_no_notice_when_record_not_requested(self, tmp_path):
        bot = Qirabot(report_dir=str(tmp_path))
        bot._log.append(dict(_LOG_ENTRY))
        out = bot.report(str(tmp_path / "report.html"))
        markup = Path(out).read_text(encoding="utf-8")
        assert "Recording was requested but not produced" not in markup


# --------------------------------------------------------------------------- #
# Report survives Ctrl+C during shutdown
# --------------------------------------------------------------------------- #
class _InterruptingRecorder:
    """Recorder whose stop() raises KeyboardInterrupt — simulates a Ctrl+C
    landing while ffmpeg is being finalized in close()."""

    def __init__(self, output, *, fps=12, capture_cursor=True, window=None, region=None, audio=False, audio_offset=None):
        self.output = output

    def start(self):
        return True

    @property
    def active(self):
        return False

    def stop(self, timeout=10.0):
        raise KeyboardInterrupt()


class TestReportSurvivesCtrlC:
    def test_report_written_when_recording_stop_interrupted(self, monkeypatch, tmp_path):
        # A Ctrl+C during recording finalize must not skip the report.
        monkeypatch.setattr(client_mod, "ScreenRecorder", _InterruptingRecorder)
        bot = Qirabot(record=True, report_dir=str(tmp_path))
        bot._log.append(dict(_LOG_ENTRY))
        report_path = Path(bot.report_dir) / "report.html"

        bot.close()  # must not raise, and must still produce the report

        assert report_path.exists()

    def test_close_swallows_keyboardinterrupt_from_report_write(self, monkeypatch, tmp_path):
        # The fallback path (non-main-thread, where SIGINT suppression is a
        # no-op): a KeyboardInterrupt raised inside the write is swallowed so
        # close() finishes its teardown instead of aborting.
        bot = Qirabot(report_dir=str(tmp_path))
        bot._log.append(dict(_LOG_ENTRY))

        def boom(out=None):
            raise KeyboardInterrupt()

        monkeypatch.setattr(bot, "_write_report", boom)
        bot.close()  # does not raise
        assert bot._closed is True

    def test_suppress_sigint_ignores_then_restores(self):
        import signal

        original = signal.getsignal(signal.SIGINT)
        with client_mod._suppress_sigint():
            assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
        assert signal.getsignal(signal.SIGINT) is original
