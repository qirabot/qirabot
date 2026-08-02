"""Tests for CLI option wiring (no network, no real Appium/devices)."""

import sys
import types
from unittest.mock import MagicMock

import click
import pytest
from click.testing import CliRunner


class _FakeOptions:
    """Stand-in for XCUITestOptions/UiAutomator2Options that records attrs set."""


@pytest.fixture
def fake_appium(monkeypatch):
    """Inject a fake ``appium`` package so the android/ios commands import cleanly.

    Returns the MagicMock used for ``webdriver.Remote`` so tests can inspect the
    options object the command built and handed to it.
    """
    remote = MagicMock(name="Remote", return_value=MagicMock(name="driver"))

    appium = types.ModuleType("appium")
    webdriver = types.ModuleType("appium.webdriver")
    webdriver.Remote = remote
    appium.webdriver = webdriver

    options_pkg = types.ModuleType("appium.options")
    ios_mod = types.ModuleType("appium.options.ios")
    ios_mod.XCUITestOptions = _FakeOptions
    android_mod = types.ModuleType("appium.options.android")
    android_mod.UiAutomator2Options = _FakeOptions

    for name, mod in {
        "appium": appium,
        "appium.webdriver": webdriver,
        "appium.options": options_pkg,
        "appium.options.ios": ios_mod,
        "appium.options.android": android_mod,
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)

    return remote


@pytest.fixture
def stub_bot(monkeypatch):
    """Bypass bot construction / AI run so the command exercises only wiring."""
    from qirabot.cli import main

    monkeypatch.setattr(main, "_make_bot", lambda *a, **k: MagicMock(name="bot"))
    monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)


def _invoke(args):
    from qirabot.cli.main import cli

    return CliRunner().invoke(cli, args)


def test_ios_bundle_id_is_passed_to_options(fake_appium, stub_bot):
    result = _invoke([
        "ios", "Send hi to honey", "--appium-url", "http://localhost:4723",
        "--bundle-id", "com.tencent.xin",
    ])
    assert result.exit_code == 0, result.output

    options = fake_appium.call_args.kwargs["options"]
    assert options.bundle_id == "com.tencent.xin"


def test_ios_without_bundle_id_sets_no_bundle(fake_appium, stub_bot):
    result = _invoke([
        "ios", "do something", "--appium-url", "http://localhost:4723",
    ])
    assert result.exit_code == 0, result.output

    options = fake_appium.call_args.kwargs["options"]
    assert not hasattr(options, "bundle_id")


def test_android_app_launch_flags_are_passed_to_options(fake_appium, stub_bot):
    result = _invoke([
        "android", "Open Display settings",
        "--appium-url", "http://localhost:4723",  # explicit flag selects Appium
        "--device", "emulator-5554",
        "--app-package", "com.android.settings",
        "--app-activity", ".Settings",
    ])
    assert result.exit_code == 0, result.output

    options = fake_appium.call_args.kwargs["options"]
    assert options.device_name == "emulator-5554"
    assert options.app_package == "com.android.settings"
    assert options.app_activity == ".Settings"


@pytest.fixture
def fake_adb(monkeypatch):
    """Script AdbDevice._run so the android command sees one ready emulator.

    Returns the recorded per-device arg lists (e.g. ["shell", "input tap ..."]).
    """
    import subprocess

    from qirabot.adb import AdbDevice

    calls = []

    def fake_run(self, args, *, scoped=True, timeout=30.0, check=True):
        calls.append(list(args))
        if args == ["devices"]:
            out = b"List of devices attached\nemulator-5554\tdevice\n"
            return subprocess.CompletedProcess([], 0, out, b"")
        return subprocess.CompletedProcess([], 0, b"", b"")

    monkeypatch.setattr(AdbDevice, "_run", fake_run)
    monkeypatch.setattr(AdbDevice, "adb_path", property(lambda self: "/fake/adb"))
    return calls


@pytest.fixture
def run_local_spy(monkeypatch):
    """stub_bot alternative that also captures the target handed to _run_local."""
    from qirabot.cli import main

    captured = {}
    monkeypatch.setattr(main, "_make_bot", lambda *a, **k: MagicMock(name="bot"))

    def spy(bot, target, *a, **k):
        captured["target"] = target

    monkeypatch.setattr(main, "_run_local", spy)
    return captured


class TestAndroidDirectEngine:
    """android defaults to the built-in adb backend; an explicit --appium-url
    switches to Appium. No --engine flag anymore."""

    def test_defaults_to_adb_device_target(self, fake_adb, fake_appium, run_local_spy):
        from qirabot.adb import AdbDevice

        result = _invoke(["android", "Open settings"])
        assert result.exit_code == 0, result.output

        target = run_local_spy["target"]
        assert isinstance(target, AdbDevice)
        assert target.serial == "emulator-5554"  # auto-picked single device
        fake_appium.assert_not_called()

    def test_explicit_serial_is_used(self, fake_adb, run_local_spy):
        result = _invoke(["android", "do it", "-d", "emulator-5554"])
        assert result.exit_code == 0, result.output
        assert run_local_spy["target"].serial == "emulator-5554"

    def test_app_package_with_activity_uses_am_start(self, fake_adb, run_local_spy):
        result = _invoke([
            "android", "do it",
            "--app-package", "com.android.settings",
            "--app-activity", ".Settings",
        ])
        assert result.exit_code == 0, result.output
        shells = [a[1] for a in fake_adb if a and a[0] == "shell"]
        assert "am start -W -n com.android.settings/.Settings" in shells

    def test_app_package_without_activity_uses_monkey(self, fake_adb, run_local_spy):
        result = _invoke(["android", "do it", "--app-package", "com.android.settings"])
        assert result.exit_code == 0, result.output
        shells = [a[1] for a in fake_adb if a and a[0] == "shell"]
        assert (
            "monkey -p com.android.settings -c android.intent.category.LAUNCHER 1"
            in shells
        )

    def test_no_devices_fails_setup_with_hint(self, monkeypatch):
        import subprocess

        from qirabot.adb import AdbDevice
        from qirabot.cli import main

        def no_devices(self, args, *, scoped=True, timeout=30.0, check=True):
            return subprocess.CompletedProcess(
                [], 0, b"List of devices attached\n", b""
            )

        monkeypatch.setattr(AdbDevice, "_run", no_devices)
        monkeypatch.setattr(AdbDevice, "adb_path", property(lambda self: "/fake/adb"))
        bot = MagicMock(name="bot")
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: bot)

        result = _invoke(["android", "do something"])

        assert result.exit_code == 1
        assert "adb devices" in result.output  # actionable hint
        bot.fail.assert_called_once()
        bot.close.assert_called_once()

    def test_explicit_appium_url_selects_appium(self, fake_appium, stub_bot):
        result = _invoke([
            "android", "do it", "--appium-url", "http://localhost:4723",
        ])
        assert result.exit_code == 0, result.output
        fake_appium.assert_called_once()

    def test_record_flag_accepted(self, fake_adb, run_local_spy):
        result = _invoke(["android", "do it", "--record"])
        assert result.exit_code == 0, result.output


@pytest.fixture
def fake_wda(monkeypatch):
    """Replace qirabot.wda.WdaClient with a recording fake (no HTTP).

    Returns the MagicMock class; .return_value is the client instance the ios
    command builds (is_ready defaults to True).
    """
    import qirabot.wda

    client = MagicMock(name="wda_client")
    client.is_ready.return_value = True
    cls = MagicMock(name="WdaClient", return_value=client)
    monkeypatch.setattr(qirabot.wda, "WdaClient", cls)
    return cls


class TestIosDirectEngine:
    """ios defaults to the built-in direct-WDA engine — no Appium server, no
    facebook-wda."""

    def test_ios_defaults_to_wda_direct(self, fake_wda, fake_appium, run_local_spy):
        result = _invoke(["ios", "Send hi to honey", "--bundle-id", "com.tencent.xin"])
        assert result.exit_code == 0, result.output

        fake_wda.assert_called_once_with("http://127.0.0.1:8100")
        client = fake_wda.return_value
        client.app_launch.assert_called_once_with("com.tencent.xin")
        assert run_local_spy["target"] is client
        fake_appium.assert_not_called()

    def test_ios_custom_wda_url(self, fake_wda, stub_bot):
        result = _invoke(["ios", "do it", "--wda-url", "http://10.0.0.5:8100"])
        assert result.exit_code == 0, result.output

        fake_wda.assert_called_once_with("http://10.0.0.5:8100")
        fake_wda.return_value.app_launch.assert_not_called()

    def test_ios_wda_down_fails_setup_with_hint(self, fake_wda, monkeypatch):
        from qirabot.cli import main

        bot = MagicMock(name="bot")
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: bot)
        fake_wda.return_value.is_ready.return_value = False

        result = _invoke(["ios", "do it"])

        assert result.exit_code == 1
        assert "WDA is not running" in result.output
        assert "--appium-url" in result.output  # the Appium escape hatch
        fake_wda.return_value.app_launch.assert_not_called()
        bot.fail.assert_called_once()

    def test_ios_device_flag_selects_appium(self, fake_appium, stub_bot):
        result = _invoke(["ios", "do it", "--device", "iPhone 15"])
        assert result.exit_code == 0, result.output

        options = fake_appium.call_args.kwargs["options"]
        assert options.device_name == "iPhone 15"



class TestDesktopWindowsEngine:
    """desktop --window-title/--hwnd: built-in Window backend selection,
    Windows-only guard, resolution errors, and setup-failure reporting."""

    @pytest.fixture
    def win_platform(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "win32")

    def test_window_title_selects_window_backend(self, run_local_spy, win_platform, monkeypatch):
        import qirabot.windows as win_mod

        monkeypatch.setattr(win_mod, "list_visible_windows", lambda: [(9, "原神 1.0")])
        result = _invoke(["desktop", "do it", "--window-title", "原神"])

        assert result.exit_code == 0, result.output
        target = run_local_spy["target"]
        from qirabot.windows import Window

        assert isinstance(target, Window)
        assert target.hwnd == 9

    def test_hwnd_binds_by_handle(self, run_local_spy, win_platform):
        result = _invoke(["desktop", "do it", "--hwnd", "1234"])

        assert result.exit_code == 0, result.output
        assert run_local_spy["target"].hwnd == 1234

    def test_window_title_and_hwnd_are_mutually_exclusive(self, win_platform):
        result = _invoke(["desktop", "do it", "--window-title", "x", "--hwnd", "1"])

        assert result.exit_code != 0
        assert "mutually exclusive" in result.output

    def test_ambiguous_largest_picks_biggest_matching_window(self, run_local_spy, win_platform, monkeypatch):
        import qirabot.windows as win_mod

        monkeypatch.setattr(
            win_mod, "list_visible_windows",
            lambda: [(1, "Genshin Impact - Launcher"), (2, "Genshin Impact")],
        )
        monkeypatch.setattr(win_mod, "_window_area", lambda h: {1: 100, 2: 10_000}[h])

        result = _invoke(
            ["desktop", "do it", "--window-title", "Genshin Impact", "--ambiguous", "largest"]
        )

        assert result.exit_code == 0, result.output
        assert run_local_spy["target"].hwnd == 2

    def test_ambiguous_default_error_names_the_cli_flag(self, monkeypatch, win_platform):
        # Without --ambiguous, several matches must fail — and the message
        # must point at spellings that exist in the CLI, not just the SDK.
        import qirabot.windows as win_mod
        from qirabot.cli import main

        bot = MagicMock(name="bot")
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: bot)
        monkeypatch.setattr(
            win_mod, "list_visible_windows",
            lambda: [(1, "Genshin Impact - Launcher"), (2, "Genshin Impact")],
        )

        result = _invoke(["desktop", "do it", "--window-title", "Genshin Impact"])

        assert result.exit_code == 1
        assert "--ambiguous largest" in result.output
        assert "--hwnd" in result.output
        bot.fail.assert_called_once()

    def test_ambiguous_largest_requires_window_title(self, win_platform):
        result = _invoke(["desktop", "do it", "--hwnd", "1234", "--ambiguous", "largest"])

        assert result.exit_code != 0
        assert "--window-title" in result.output

    def test_ambiguous_rejects_unknown_value(self, win_platform):
        result = _invoke(["desktop", "do it", "--window-title", "x", "--ambiguous", "biggest"])

        assert result.exit_code != 0
        assert "largest" in result.output  # click.Choice lists the valid values

    def test_rejected_on_non_windows_with_guidance(self, monkeypatch):
        # A Mac user passing --window-title is expected behavior: the error
        # must explain the platform boundary and the workable alternative.
        monkeypatch.setattr(sys, "platform", "darwin")

        result = _invoke(["desktop", "do it", "--window-title", "WeChat"])

        assert result.exit_code != 0
        assert "only exists on Windows" in result.output
        assert "full-screen" in result.output

    def test_resolution_failure_reports_fail_with_titles(self, monkeypatch, win_platform):
        import qirabot.windows as win_mod
        from qirabot.cli import main

        bot = MagicMock(name="bot")
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: bot)
        monkeypatch.setattr(
            win_mod, "list_visible_windows", lambda: [(1, "Notepad")]
        )

        result = _invoke(["desktop", "do it", "--window-title", "nope"])

        assert result.exit_code == 1
        assert "Notepad" in result.output  # lists what IS visible
        bot.fail.assert_called_once()
        bot.close.assert_called_once()

    def test_app_launched_before_resolution(self, monkeypatch, win_platform):
        import qirabot.windows as win_mod
        from qirabot.cli import main

        calls: list[str] = []
        monkeypatch.setattr(
            "qirabot.launch_app", lambda app, wait=2.0: calls.append("launch")
        )
        monkeypatch.setattr(
            win_mod, "list_visible_windows",
            lambda: calls.append("resolve") or [(7, "Game")],
        )
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: MagicMock(name="bot"))
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)

        result = _invoke(
            ["desktop", "do it", "--app", "C:/game.exe", "--window-title", "Game"]
        )

        assert result.exit_code == 0, result.output
        assert calls == ["launch", "resolve"]

    def _capture_make_bot(self, monkeypatch):
        from qirabot.cli import main

        captured = {}

        def fake_make_bot(ctx, **kwargs):
            captured.update(kwargs)
            return MagicMock(name="bot")

        monkeypatch.setattr(main, "_make_bot", fake_make_bot)
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)
        return captured

    def test_record_follows_bound_window(self, monkeypatch, win_platform):
        """--record with a bound window must thread record_window=True to the
        SDK — otherwise ffmpeg grabs the full screen despite the binding."""
        captured = self._capture_make_bot(monkeypatch)

        result = _invoke(["desktop", "do it", "--hwnd", "1234", "--record"])

        assert result.exit_code == 0, result.output
        assert captured["record"] is True
        assert captured["record_window"] is True

    def test_full_screen_desktop_leaves_record_window_off(self, monkeypatch):
        """Without a window binding there is no window to follow — the
        pyautogui path must not set record_window."""
        captured = self._capture_make_bot(monkeypatch)
        monkeypatch.setitem(sys.modules, "pyautogui", types.ModuleType("pyautogui"))

        result = _invoke(["desktop", "do it", "--record"])

        assert result.exit_code == 0, result.output
        assert captured["record"] is True
        assert not captured.get("record_window")


class TestEngineFlagValidation:
    """Engine-specific URL/device flags are rejected under the other engine
    (only when explicitly passed — defaults never trip the guard)."""

    def test_ios_appium_rejects_mjpeg_url(self, fake_appium, stub_bot):
        result = _invoke([
            "ios", "do it", "--appium-url", "http://x:4723",
            "--record", "--mjpeg-url", "http://x:9100",
        ])

        assert result.exit_code != 0
        assert "--mjpeg-url" in result.output

    def test_ios_mjpeg_url_requires_record(self, stub_bot):
        result = _invoke(["ios", "do it", "--mjpeg-url", "http://x:9100"])

        assert result.exit_code != 0
        assert "--record" in result.output

    def test_ios_appium_rejects_wda_url(self, fake_appium, stub_bot):
        result = _invoke([
            "ios", "do it", "--appium-url", "http://x:4723",
            "--wda-url", "http://x:8100",
        ])

        assert result.exit_code != 0
        assert "direct engine" in result.output


class TestDeviceRecording:
    """--record on android/ios: recorder-source selection per engine (WDA MJPEG
    with its fail-fast check, adb screenrecord, Appium recording API) and the
    flag threading into _make_bot."""

    def _capture(self, monkeypatch):
        from qirabot.cli import main

        captured = {"make_bot": {}, "checked_url": None}

        def fake_make_bot(ctx, **kwargs):
            captured["make_bot"].update(kwargs)
            return MagicMock(name="bot")

        def fake_check(url):
            captured["checked_url"] = url

        monkeypatch.setattr(main, "_make_bot", fake_make_bot)
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)
        monkeypatch.setattr(main, "_check_mjpeg_ready", fake_check)
        return captured

    def test_record_derives_mjpeg_url_from_wda_url(self, fake_wda, monkeypatch):
        captured = self._capture(monkeypatch)

        result = _invoke(["ios", "do it", "--record"])

        assert result.exit_code == 0, result.output
        assert captured["checked_url"] == "http://127.0.0.1:9100"
        assert captured["make_bot"]["record"] is True
        assert captured["make_bot"]["record_mjpeg_url"] == "http://127.0.0.1:9100"

    def test_record_follows_wda_host(self, fake_wda, monkeypatch):
        captured = self._capture(monkeypatch)

        result = _invoke(["ios", "do it", "--record", "--wda-url", "http://10.0.0.5:8100"])

        assert result.exit_code == 0, result.output
        assert captured["checked_url"] == "http://10.0.0.5:9100"

    def test_explicit_mjpeg_url_wins(self, fake_wda, monkeypatch):
        captured = self._capture(monkeypatch)

        result = _invoke(["ios", "do it", "--record", "--mjpeg-url", "http://10.0.0.5:9200"])

        assert result.exit_code == 0, result.output
        assert captured["checked_url"] == "http://10.0.0.5:9200"
        assert captured["make_bot"]["record_mjpeg_url"] == "http://10.0.0.5:9200"

    def test_no_record_skips_check_and_recording(self, fake_wda, monkeypatch):
        captured = self._capture(monkeypatch)

        result = _invoke(["ios", "do it"])

        assert result.exit_code == 0, result.output
        assert captured["checked_url"] is None
        assert captured["make_bot"]["record"] is False
        assert captured["make_bot"]["record_mjpeg_url"] == ""

    def test_android_record_uses_adb_screenrecord(self, fake_adb, monkeypatch):
        captured = self._capture(monkeypatch)

        result = _invoke(["android", "do it", "--record"])

        assert result.exit_code == 0, result.output
        assert captured["make_bot"]["record"] is True
        assert captured["make_bot"]["record_device"] is True
        # No MJPEG involved on android — that's the iOS/WDA path.
        assert captured["checked_url"] is None

    def test_android_appium_record_threads_device_recording(self, fake_appium, monkeypatch):
        captured = self._capture(monkeypatch)

        result = _invoke([
            "android", "do it", "--appium-url", "http://localhost:4723", "--record",
        ])

        assert result.exit_code == 0, result.output
        assert captured["make_bot"]["record"] is True
        assert captured["make_bot"]["record_device"] is True

    def test_ios_appium_record_threads_device_recording(self, fake_appium, monkeypatch):
        captured = self._capture(monkeypatch)

        result = _invoke([
            "ios", "do it", "--appium-url", "http://localhost:4723", "--record",
        ])

        assert result.exit_code == 0, result.output
        assert captured["make_bot"]["record"] is True
        assert captured["make_bot"]["record_device"] is True

    def test_appium_recording_stopped_before_driver_quit(self, fake_appium, monkeypatch):
        # The Appium video lives in the session: bot.stop_recording() must run
        # before driver.quit() or the recording is lost.
        from qirabot.cli import main

        order = []
        bot = MagicMock(name="bot")
        bot.stop_recording.side_effect = lambda: order.append("stop_recording")
        driver = fake_appium.return_value
        driver.quit.side_effect = lambda: order.append("quit")

        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: bot)
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)

        result = _invoke([
            "android", "do it", "--appium-url", "http://localhost:4723", "--record",
        ])

        assert result.exit_code == 0, result.output
        assert order == ["stop_recording", "quit"]

    def test_unreachable_stream_fails_before_task_creation(self, fake_wda, monkeypatch):
        # The probe failing must exit with the iproxy hint and never build the
        # bot (no task, no 300-step run that quietly recorded nothing).
        import qirabot.recording as recording_mod
        from qirabot.cli import main

        made = []
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: made.append(1) or MagicMock())
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)
        monkeypatch.setattr(
            recording_mod, "check_mjpeg_stream",
            lambda url, timeout=5.0: f"cannot read MJPEG stream at {url} (refused)",
        )

        result = _invoke(["ios", "do it", "--record"])

        assert result.exit_code != 0
        assert "iproxy 9100 9100" in result.output
        assert made == []


class TestRunLocalAbort:
    """A Ctrl+C during bot.ai() must be reported, never left for the caller's
    finally:bot.close() to complete the task as success. KeyboardInterrupt is a
    BaseException, so this guards the `except Exception` gap that previously let
    interrupted runs land as 'succeeded'."""

    def _stub_bot(self):
        bot = MagicMock(name="bot")
        bot.task_id = "t"
        return bot

    def test_keyboardinterrupt_reports_cancel_and_exits_130(self):
        from qirabot.cli.main import _run_local

        bot = self._stub_bot()
        bot.ai.side_effect = KeyboardInterrupt()

        with pytest.raises(SystemExit) as exc:
            _run_local(bot, object(), "send a message", max_steps=20)

        assert exc.value.code == 130
        # Ctrl+C is a deliberate cancel, not a failure.
        bot.cancel.assert_called_once()
        bot.fail.assert_not_called()
        # _run_local must not close the bot — that's the command's finally.
        bot.close.assert_not_called()

    def test_exception_still_reports_failure_and_exits_1(self):
        from qirabot.cli.main import _run_local

        bot = self._stub_bot()
        bot.ai.side_effect = RuntimeError("boom")

        with pytest.raises(SystemExit) as exc:
            _run_local(bot, object(), "send a message", max_steps=20)

        assert exc.value.code == 1
        bot.fail.assert_called_once_with("boom")

    def test_failed_result_reports_failure_and_exits_1(self):
        # A failed run must exit non-zero so scripts/CI can detect it — exiting
        # 0 here made `qirabot browser ... && deploy` silently proceed on failure.
        from qirabot.cli.main import _run_local
        from qirabot.client import RunResult

        bot = self._stub_bot()
        bot.ai.return_value = RunResult(success=False, output="max steps reached")

        with pytest.raises(SystemExit) as exc:
            _run_local(bot, object(), "send a message", max_steps=20)

        assert exc.value.code == 1
        bot.fail.assert_called_once_with("max steps reached")

    def test_success_result_does_not_fail(self):
        from qirabot.cli.main import _run_local
        from qirabot.client import RunResult

        bot = self._stub_bot()
        bot.ai.return_value = RunResult(success=True, output="done")

        _run_local(bot, object(), "send a message", max_steps=20)

        bot.fail.assert_not_called()


class TestSetupFailureReporting:
    """A failure during setup (bot.open / Appium Remote) — after the task is
    created but before _run_local takes over reporting — must be recorded as a
    failure, never left for the command's finally:bot.close() to complete as
    succeeded."""

    def test_browser_open_failure_reports_fail(self, monkeypatch):
        from qirabot.cli import main

        bot = MagicMock(name="bot")
        bot.open.side_effect = RuntimeError("chromium launch failed")
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: bot)

        result = _invoke(["browser", "do something", "--url", "example.com"])

        assert result.exit_code == 1
        bot.fail.assert_called_once()
        bot.close.assert_called_once()

    @pytest.mark.parametrize(
        "args",
        [
            ["android", "do something", "--appium-url", "http://localhost:4723"],
            ["ios", "do something", "--appium-url", "http://localhost:4723"],
        ],
        ids=["android", "ios"],
    )
    def test_appium_remote_failure_reports_fail(self, fake_appium, monkeypatch, args):
        from qirabot.cli import main

        bot = MagicMock(name="bot")
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: bot)
        fake_appium.side_effect = RuntimeError("appium server unreachable")

        result = _invoke(args)

        assert result.exit_code == 1
        bot.fail.assert_called_once()
        bot.close.assert_called_once()

    def test_quit_error_after_success_is_not_a_failure(self, fake_appium, monkeypatch):
        # driver.quit() raising after a successful run must NOT be turned into a
        # task failure — the setup except is scoped to Remote() only.
        from qirabot.cli import main

        bot = MagicMock(name="bot")
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: bot)
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)
        driver = MagicMock(name="driver")
        driver.quit.side_effect = RuntimeError("quit boom")
        fake_appium.return_value = driver

        _invoke([
            "android", "do something", "--appium-url", "http://localhost:4723",
        ])

        bot.fail.assert_not_called()
        bot.close.assert_called_once()


class TestEntryPoint:
    def test_main_loads_dotenv_before_parsing(self, monkeypatch):
        """main() must load .env before click parses options, so envvar
        fallbacks (QIRA_MODEL, QIRA_VERTEX_PROJECT, ...) can pick up values
        from the file."""
        from qirabot.cli import main as cli_main

        called = []
        monkeypatch.setattr(cli_main, "load_dotenv", lambda: called.append(True))
        monkeypatch.setattr(sys, "argv", ["qirabot", "--help"])

        with pytest.raises(SystemExit) as exc:
            cli_main.main()

        assert exc.value.code == 0
        assert called == [True]

    def test_qirabot_error_escaping_command_prints_one_line(self, monkeypatch, capsys):
        """SDK errors that escape a command body (lazy imports deep inside)
        must become a one-line message, not a traceback."""
        from qirabot.cli import main as cli_main
        from qirabot.exceptions import QirabotError

        def boom(ctx):
            raise QirabotError("engine exploded mid-probe")

        monkeypatch.setattr(cli_main, "_resolve_adc", boom)
        monkeypatch.setattr(cli_main, "load_dotenv", lambda: False)
        monkeypatch.setattr(sys, "argv", ["qirabot", "models"])

        with pytest.raises(SystemExit) as exc:
            cli_main.main()

        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "engine exploded mid-probe" in err
        assert "Traceback" not in err


class TestMakeBotErrors:
    def test_construction_error_prints_engine_message(self, monkeypatch):
        """No string-sniffing: the engine's construction error (already
        actionable — missing ADC, unknown provider, missing project) is
        printed verbatim, exit code 1."""
        import qirabot

        def boom(**kwargs):
            raise ValueError(
                'unknown provider "nope"; use model="{provider}/{model}"'
            )

        monkeypatch.setattr(qirabot, "Qirabot", boom)

        result = _invoke(["browser", "do something"])

        assert result.exit_code == 1
        assert 'unknown provider "nope"' in result.output


class TestModelsCommand:
    """models is local-only now: it prints the built-in provider table and the
    ADC probe result — no network LLM call, no server."""

    @staticmethod
    def _flat(result):
        return " ".join(result.output.split())

    def test_lists_all_three_providers(self, monkeypatch):
        from qirabot.cli import main

        monkeypatch.setattr(main, "_resolve_adc", lambda ctx: ("my-proj", ""))

        result = _invoke(["models"])

        assert result.exit_code == 0, result.output
        for provider in ("claude-vertex", "gemini-vertex", "vertex-openai"):
            assert provider in result.output

    def test_adc_ok_prints_project(self, monkeypatch):
        from qirabot.cli import main

        monkeypatch.setattr(main, "_resolve_adc", lambda ctx: ("my-proj", ""))

        result = _invoke(["models"])

        out = self._flat(result)
        assert result.exit_code == 0, result.output
        assert "Google Cloud credentials OK" in out
        assert "my-proj" in out

    def test_adc_failure_is_reported(self, monkeypatch):
        from qirabot.cli import main

        monkeypatch.setattr(
            main, "_resolve_adc",
            lambda ctx: ("", "no credentials were found (run `gcloud auth application-default login`)"),
        )

        result = _invoke(["models"])

        out = self._flat(result)
        assert result.exit_code == 0, result.output  # informational, not a gate
        assert "no credentials were found" in out

    def test_session_default_model_is_shown(self, monkeypatch):
        from qirabot.cli import main
        from qirabot.engine.providers.registry import resolve_default_model

        monkeypatch.setattr(main, "_resolve_adc", lambda ctx: ("p", ""))

        result = _invoke(["models"])

        assert result.exit_code == 0, result.output
        assert "Session default" in self._flat(result)
        assert resolve_default_model() in self._flat(result)


class TestHelpers:
    def test_wda_mjpeg_url_defaults(self):
        from qirabot.cli.main import _wda_mjpeg_url

        assert _wda_mjpeg_url("http://127.0.0.1:8100") == "http://127.0.0.1:9100"
        assert _wda_mjpeg_url("http://10.0.0.5:8100") == "http://10.0.0.5:9100"
        # Scheme-less values (users paste host:port) still resolve the host.
        assert _wda_mjpeg_url("10.0.0.5:8100") == "http://10.0.0.5:9100"

    def test_default_task_name_derives_and_truncates(self):
        from qirabot.cli.main import _default_task_name

        assert _default_task_name("Click login") == "Click login"
        # first non-blank line, stripped
        assert _default_task_name("  \n  Open the menu\nthen tap Save") == "Open the menu"
        # capped at 60 chars
        assert _default_task_name("x" * 100) == "x" * 60
        # blank instruction falls back to a stable default
        assert _default_task_name("   \n  ") == "cli"


class TestRunOptionWiring:
    """--name derivation, --report/--record and the model/language/thinking
    task options are threaded to _make_bot; --max-steps reaches _run_local."""

    def _capture_make_bot(self, monkeypatch):
        from qirabot.cli import main

        captured = {}

        def fake_make_bot(ctx, **kwargs):
            captured["ctx"] = ctx
            captured.update(kwargs)
            return MagicMock(name="bot")

        monkeypatch.setattr(main, "_make_bot", fake_make_bot)
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)
        return captured

    def test_name_defaults_to_instruction(self, monkeypatch):
        captured = self._capture_make_bot(monkeypatch)

        result = _invoke(["browser", "Find the cheapest flight to Tokyo"])

        assert result.exit_code == 0, result.output
        assert captured["task_name"] == "Find the cheapest flight to Tokyo"
        assert captured["report"] is True
        assert captured["record"] is False

    def test_explicit_name_and_no_report(self, monkeypatch):
        captured = self._capture_make_bot(monkeypatch)

        result = _invoke(["browser", "do it", "--name", "smoke-test", "--no-report", "--record"])

        assert result.exit_code == 0, result.output
        assert captured["task_name"] == "smoke-test"
        assert captured["report"] is False
        assert captured["record"] is True

    def test_model_thinking_language_threaded(self, monkeypatch):
        captured = self._capture_make_bot(monkeypatch)

        result = _invoke([
            "browser", "do it",
            "-m", "gemini-vertex/gemini-3-flash-preview",
            "--thinking-level", "high",
            "-l", "zh",
        ])

        assert result.exit_code == 0, result.output
        assert captured["model"] == "gemini-vertex/gemini-3-flash-preview"
        assert captured["thinking_level"] == "high"
        assert captured["language"] == "zh"

    def test_defaults_leave_model_to_the_engine(self, monkeypatch):
        # No -m: the CLI passes "" through and the engine resolves QIRA_MODEL /
        # the built-in default — the CLI must not bake in a model of its own.
        captured = self._capture_make_bot(monkeypatch)

        result = _invoke(["browser", "do it"])

        assert result.exit_code == 0, result.output
        assert captured["model"] == ""
        assert captured["thinking_level"] == ""
        assert captured["language"] == ""

    def test_vertex_project_and_location_reach_ctx(self, monkeypatch):
        captured = self._capture_make_bot(monkeypatch)

        result = _invoke([
            "--vertex-project", "my-proj", "--vertex-location", "europe-west1",
            "browser", "do it",
        ])

        assert result.exit_code == 0, result.output
        assert captured["ctx"].obj["vertex_project"] == "my-proj"
        assert captured["ctx"].obj["vertex_location"] == "europe-west1"

    def test_vertex_env_vars_are_the_fallback(self, monkeypatch):
        captured = self._capture_make_bot(monkeypatch)
        monkeypatch.setenv("QIRA_VERTEX_PROJECT", "env-proj")
        monkeypatch.setenv("QIRA_VERTEX_LOCATION", "us-central1")

        result = _invoke(["browser", "do it"])

        assert result.exit_code == 0, result.output
        assert captured["ctx"].obj["vertex_project"] == "env-proj"
        assert captured["ctx"].obj["vertex_location"] == "us-central1"

    def test_max_steps_reaches_run_local(self, monkeypatch):
        from qirabot.cli import main

        captured = {}
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: MagicMock(name="bot"))

        def spy(bot, target, instruction, max_steps, **kwargs):
            captured["max_steps"] = max_steps

        monkeypatch.setattr(main, "_run_local", spy)

        result = _invoke(["browser", "do it", "--max-steps", "7"])

        assert result.exit_code == 0, result.output
        assert captured["max_steps"] == 7


class TestKnowledgeOption:
    """--knowledge/-k: files resolve to text at parse time (before any task is
    created), merge across repeats, and thread through to bot.ai()."""

    def _capture_run_local(self, monkeypatch):
        from qirabot.cli import main

        captured = {}
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: MagicMock(name="bot"))

        def spy(bot, target, instruction, max_steps, **kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(main, "_run_local", spy)
        return captured

    def test_single_file_resolves_to_text(self, monkeypatch):
        captured = self._capture_run_local(monkeypatch)

        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("rules.md", "w", encoding="utf-8") as f:
                f.write("GM commands may be used once per match.")

            from qirabot.cli.main import cli

            result = runner.invoke(cli, ["browser", "do it", "-k", "rules.md"])

        assert result.exit_code == 0, result.output
        assert captured["knowledge"] == "GM commands may be used once per match."

    def test_multiple_files_merge_in_order(self, monkeypatch):
        captured = self._capture_run_local(monkeypatch)

        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("a.md", "w", encoding="utf-8") as f:
                f.write("combat rules")
            with open("b.md", "w", encoding="utf-8") as f:
                f.write("gm policy")

            from qirabot.cli.main import cli

            result = runner.invoke(
                cli, ["browser", "do it", "-k", "a.md", "-k", "b.md"]
            )

        assert result.exit_code == 0, result.output
        assert captured["knowledge"] == "combat rules\n\ngm policy"

    def test_omitted_resolves_to_empty(self, monkeypatch):
        captured = self._capture_run_local(monkeypatch)

        result = _invoke(["browser", "do it"])

        assert result.exit_code == 0, result.output
        assert captured["knowledge"] == ""

    def test_threads_through_appium_engine(self, fake_appium, monkeypatch):
        captured = self._capture_run_local(monkeypatch)

        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("k.md", "w", encoding="utf-8") as f:
                f.write("android knowledge")

            from qirabot.cli.main import cli

            result = runner.invoke(
                cli,
                [
                    "android", "do it",
                    "--appium-url", "http://localhost:4723", "-k", "k.md",
                ],
            )

        assert result.exit_code == 0, result.output
        assert captured["knowledge"] == "android knowledge"

    def test_missing_file_fails_before_bot_creation(self, monkeypatch):
        from qirabot.cli import main

        made = []
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: made.append(1) or MagicMock())

        result = _invoke(["browser", "do it", "-k", "nope.md"])

        assert result.exit_code != 0
        assert "nope.md" in result.output
        assert made == []

    def test_over_limit_fails_with_byte_breakdown(self, monkeypatch):
        from qirabot.cli import main

        made = []
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: made.append(1) or MagicMock())

        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("big.md", "w", encoding="utf-8") as f:
                f.write("x" * (33 * 1024))

            from qirabot.cli.main import cli

            result = runner.invoke(cli, ["browser", "do it", "-k", "big.md"])

        assert result.exit_code != 0
        assert "exceeds the 32768-byte limit" in result.output
        assert "big.md" in result.output  # names the file to trim
        assert made == []

    def test_non_utf8_file_fails_with_filename(self, monkeypatch):
        from qirabot.cli import main

        made = []
        monkeypatch.setattr(main, "_make_bot", lambda *a, **k: made.append(1) or MagicMock())

        runner = CliRunner()
        with runner.isolated_filesystem():
            with open("bin.md", "wb") as f:
                f.write(b"\xff\xfe\x00garbage")

            from qirabot.cli.main import cli

            result = runner.invoke(cli, ["browser", "do it", "-k", "bin.md"])

        assert result.exit_code != 0
        assert "not UTF-8" in result.output
        assert made == []

    def test_run_local_passes_none_when_empty(self):
        """bot.ai must see knowledge=None (not "") when no -k was given, so the
        SDK skips knowledge handling entirely."""
        from qirabot.cli.main import _run_local
        from qirabot.client import RunResult

        bot = MagicMock(name="bot")
        bot.task_id = ""
        bot.ai.return_value = RunResult(success=True, output="done")

        _run_local(bot, object(), "do it", max_steps=20)
        assert bot.ai.call_args.kwargs["knowledge"] is None

        _run_local(bot, object(), "do it", max_steps=20, knowledge="the rules")
        assert bot.ai.call_args.kwargs["knowledge"] == "the rules"


class TestMobileSplit:
    """`mobile` was split into `android`/`ios`; cross-platform flags are now
    rejected by click itself as unknown options instead of hand-rolled guards."""

    def test_mobile_command_is_gone(self):
        from qirabot.cli.main import cli

        ctx = click.Context(cli)
        commands = cli.list_commands(ctx)
        assert "android" in commands
        assert "ios" in commands
        assert "mobile" not in commands
        assert cli.get_command(ctx, "mobile") is None

    def test_ios_rejects_android_flags(self, fake_appium, stub_bot):
        result = _invoke(["ios", "do it", "--app-package", "com.x"])

        assert result.exit_code != 0
        assert "--app-package" in result.output

    def test_android_rejects_bundle_id(self, fake_appium, stub_bot):
        result = _invoke(["android", "do it", "--bundle-id", "com.x"])

        assert result.exit_code != 0
        assert "--bundle-id" in result.output

    def test_ios_has_no_d_short_for_device(self, fake_appium, stub_bot):
        """On ios, --device switches the engine to Appium — that must be typed
        out deliberately, not inherited as -d muscle memory from android."""
        result = _invoke(["ios", "do it", "-d", "iPhone 15"])

        assert result.exit_code != 0
        assert "No such option" in result.output


class TestRemovedCloudCommands:
    """v3 dropped the cloud backend: login/task/screenshot and the cloud
    connection flags must be gone from the CLI surface."""

    @pytest.mark.parametrize("command", ["login", "task", "screenshot"])
    def test_cloud_command_is_gone(self, command):
        from qirabot.cli.main import cli

        ctx = click.Context(cli)
        assert command not in cli.list_commands(ctx)
        assert cli.get_command(ctx, command) is None

    @pytest.mark.parametrize(
        "flag", ["--api-key", "--base-url", "--timeout", "--verify-ssl"]
    )
    def test_cloud_global_flag_is_gone(self, flag):
        result = _invoke([flag, "x", "models"])

        assert result.exit_code != 0
        assert "No such option" in result.output


class TestGroupedHelp:
    """Task-command --help renders options under group headings so the shared
    surface (task + report/debug groups) is recognizable across platforms."""

    @pytest.mark.parametrize(
        ("command", "platform_heading"),
        [
            ("browser", "Browser options:"),
            ("android", "Android options:"),
            ("ios", "iOS options:"),
            ("desktop", "Desktop options:"),
        ],
    )
    def test_help_shows_group_headings(self, command, platform_heading):
        from qirabot.cli.main import cli

        result = CliRunner().invoke(cli, [command, "-h"])

        assert result.exit_code == 0, result.output
        assert "Task options (all platforms):" in result.output
        assert platform_heading in result.output
        assert "Report & debug options (all platforms):" in result.output
        # The flat default heading must be gone — every option is grouped.
        assert "\nOptions:\n" not in result.output


class TestBrowserCommand:
    def test_browser_is_canonical_and_browse_is_gone(self):
        from qirabot.cli.main import cli

        ctx = click.Context(cli)
        commands = cli.list_commands(ctx)
        assert "browser" in commands
        # The old verb name must no longer resolve after the rename.
        assert "browse" not in commands
        assert cli.get_command(ctx, "browse") is None

    def test_browser_runs(self, stub_bot):
        result = _invoke(["browser", "do something"])

        assert result.exit_code == 0, result.output


class TestOpenBrowserCommand:
    """open-browser exists so users can log in to sites by hand once and
    persist the session in --user-data-dir — no AI task, no credentials."""

    @pytest.fixture
    def launched(self, monkeypatch):
        """Stub the launch; return (launch_browser mock, its LaunchedBrowser)."""
        from qirabot.cli import main

        monkeypatch.setattr(main, "_display_available", lambda: True)
        fake = MagicMock(name="launched")
        launch = MagicMock(name="launch_browser", return_value=fake)
        monkeypatch.setattr(main, "launch_browser", launch)
        return launch, fake

    def test_user_data_dir_is_required(self, launched):
        result = _invoke(["open-browser"])

        assert result.exit_code != 0
        assert "--user-data-dir" in result.output
        launched[0].assert_not_called()

    def test_opens_waits_and_prints_next_step(self, launched):
        launch, fake = launched
        result = _invoke([
            "open-browser",
            "--user-data-dir", "~/.automation",
            "--url", "news.ycombinator.com/login",
        ])

        assert result.exit_code == 0, result.output
        kwargs = launch.call_args.kwargs
        assert kwargs["user_data_dir"] == "~/.automation"
        assert kwargs["url"] == "news.ycombinator.com/login"
        assert kwargs["headless"] is False
        fake.context.wait_for_event.assert_called_once_with("close", timeout=0)
        fake.context.close.assert_called_once()
        fake.playwright.stop.assert_called_once()
        assert 'qirabot browser "<your task>" --user-data-dir ~/.automation' in result.output

    def test_ctrl_c_still_cleans_up_and_reports(self, launched):
        launch, fake = launched
        fake.context.wait_for_event.side_effect = KeyboardInterrupt

        result = _invoke(["open-browser", "--user-data-dir", "/tmp/p"])

        assert result.exit_code == 0, result.output
        assert "Session saved to /tmp/p" in result.output
        fake.context.close.assert_called_once()
        fake.playwright.stop.assert_called_once()

    def test_no_display_is_a_hard_error(self, launched, monkeypatch):
        """Unlike bot.open(), no headless fallback: a browser nobody can see
        or click is useless for manual login."""
        from qirabot.cli import main

        launch, _ = launched
        monkeypatch.setattr(main, "_display_available", lambda: False)

        result = _invoke(["open-browser", "--user-data-dir", "/tmp/p"])

        assert result.exit_code != 0
        assert "no display detected" in result.output
        launch.assert_not_called()


class TestDoctor:
    """doctor is pure wiring around probes — stub the probes (including the
    real-ADC one, which would otherwise hit the credential chain), assert the
    verdict."""

    _ADC_ERR = "no credentials were found (run `gcloud auth application-default login`)"

    def _run(
        self, monkeypatch, *, has=(), chromium=None, adc_ok=True,
        display=True, adb=False,
    ):
        """Invoke doctor with stubbed probes; returns the click result."""
        from qirabot.cli import main

        monkeypatch.setattr(main, "_has_module", lambda m: m.split(".")[0] in has)
        monkeypatch.setattr(main, "_chromium_status", lambda: chromium)
        monkeypatch.setattr(main, "_display_available", lambda: display)
        monkeypatch.setattr(main, "_adb_binary_found", lambda: adb)
        adc = ("my-proj", "") if adc_ok else ("", self._ADC_ERR)
        monkeypatch.setattr(main, "_resolve_adc", lambda ctx: adc)

        return CliRunner().invoke(main.cli, ["doctor"])

    @staticmethod
    def _flat(result):
        """Rich wraps long lines at the console width; normalize whitespace so
        substring assertions don't break on a wrap point."""
        return " ".join(result.output.split())

    def test_ready_when_adc_and_one_backend(self, monkeypatch):
        result = self._run(monkeypatch, has={"playwright"}, chromium="ready")

        assert result.exit_code == 0, result.output
        assert "Ready" in self._flat(result)

    def test_adc_ok_prints_project_and_model(self, monkeypatch):
        from qirabot.engine.providers.registry import resolve_default_model

        result = self._run(monkeypatch, has={"playwright"}, chromium="ready")

        out = self._flat(result)
        assert result.exit_code == 0, result.output
        assert "Google Cloud credentials OK" in out
        assert "my-proj" in out
        assert resolve_default_model() in out

    def test_nothing_installed_exits_1_with_hints(self, monkeypatch):
        result = self._run(monkeypatch, adc_ok=False)

        out = self._flat(result)
        assert result.exit_code == 1
        assert "no credentials were found" in out
        assert 'python -m pip install "qirabot[browser]" && qirabot install-browser' in out
        # Selenium is not an extra — the hint must be a plain pip install.
        assert "python -m pip install selenium" in out
        assert "qirabot[selenium]" not in out
        assert "Not ready" in out

    def test_adc_failure_is_a_problem(self, monkeypatch):
        """A working backend can't compensate for missing credentials — the
        local engine needs ADC for every run."""
        result = self._run(monkeypatch, has={"pyautogui"}, adc_ok=False)

        out = self._flat(result)
        assert result.exit_code == 1
        assert "no credentials were found" in out
        assert "Not ready" in out

    def test_playwright_without_chromium_is_not_ready(self, monkeypatch):
        """An importable playwright with no browser download can't run bot.open();
        doctor must point at the missing Chromium download step (via the
        `qirabot install-browser` wrapper — Playwright's own command isn't on
        PATH in isolated installs)."""
        result = self._run(monkeypatch, has={"playwright"}, chromium="no-browser")

        out = self._flat(result)
        assert result.exit_code == 1
        assert "qirabot install-browser" in out

    def test_no_display_notes_headless_fallback_but_stays_ready(self, monkeypatch):
        """A display-less Linux box is still a working browser environment
        (open() falls back to headless) — doctor informs, not fails."""
        result = self._run(
            monkeypatch, has={"playwright"}, chromium="ready", display=False
        )

        out = self._flat(result)
        assert result.exit_code == 0, result.output
        assert "fall back to headless" in out

    def test_display_available_prints_no_headless_note(self, monkeypatch):
        result = self._run(monkeypatch, has={"playwright"}, chromium="ready")

        assert "fall back to headless" not in self._flat(result)

    def test_chromium_missing_system_libs_is_not_ready(self, monkeypatch):
        """A downloaded Chromium whose shared libraries don't resolve (bare Linux
        server) fails at launch; the fix is install-deps, not a re-download."""
        result = self._run(monkeypatch, has={"playwright"}, chromium="no-libs")

        out = self._flat(result)
        assert result.exit_code == 1
        assert "sudo playwright install-deps chromium" in out
        assert "system libraries are missing" in out

    def test_other_backend_alone_is_ready(self, monkeypatch):
        """The default path (browser) is a recommendation, not a requirement —
        an adb-only environment is a valid, ready setup."""
        result = self._run(monkeypatch, chromium=None, adb=True)

        assert result.exit_code == 0, result.output
        assert "Ready" in self._flat(result)

    def test_missing_adb_binary_prints_platform_tools_hint(self, monkeypatch):
        result = self._run(monkeypatch, has={"playwright"}, chromium="ready", adb=False)

        out = self._flat(result)
        assert result.exit_code == 0, result.output  # browser still ready
        assert "platform-tools" in out


class TestRunLocalUserAbort:
    """The ESC-hold kill switch is the same deliberate cancel as Ctrl+C:
    yellow Cancelled + exit 130, never a red Error + fail()."""

    def _bot_raising(self, exc):
        class _Bot:
            task_id = None

            def __init__(self):
                self.failed = []
                self.cancelled = []

            def ai(self, *a, **k):
                raise exc

            def fail(self, msg=""):
                self.failed.append(msg)

            def cancel(self, msg=""):
                self.cancelled.append(msg)

        return _Bot()

    def test_esc_abort_exits_130_without_fail(self, capsys):
        from qirabot.cli import main as cli_main
        from qirabot.exceptions import QirabotError

        bot = self._bot_raising(
            QirabotError("aborted by user (ESC held)", code="user_abort")
        )
        with pytest.raises(SystemExit) as exc:
            cli_main._run_local(bot, object(), "task", max_steps=5)
        assert exc.value.code == 130
        assert bot.failed == []  # cancel() already ran inside ai()
        assert "Cancelled" in capsys.readouterr().out

    def test_other_qirabot_errors_stay_red_exit_1(self, capsys):
        from qirabot.cli import main as cli_main
        from qirabot.exceptions import QirabotError

        bot = self._bot_raising(QirabotError("boom", code="windows.sendinput_failed"))
        with pytest.raises(SystemExit) as exc:
            cli_main._run_local(bot, object(), "task", max_steps=5)
        assert exc.value.code == 1
        assert len(bot.failed) == 1 and "boom" in bot.failed[0]
        assert "Error" in capsys.readouterr().out
