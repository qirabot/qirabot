"""Terminal-outcome bookkeeping around close() — local-only in v3.

There is no /complete request anymore: fail()/cancel() latch ``_terminalized``
(first call wins, idempotent), close() does purely local cleanup, and the run's
outcome lives in ``_last_ai_status`` / ``_last_ai_error``. A run whose final
command errored must still be recorded as an error — including when close()
runs via atexit after the script crashed out of ai() without calling fail().
"""

import logging

import pytest

from qirabot.adapters.base import DeviceAdapter, DeviceInfo
from qirabot.exceptions import ActionError


class _FakeAdapter(DeviceAdapter):
    def __init__(self):
        pass

    def screenshot(self, config=None):
        return b"img"

    def click(self, x, y):
        pass

    def double_click(self, x, y):
        pass

    def type_text(self, x, y, text):
        pass

    def press_key(self, key):
        pass

    def scroll(self, x, y, direction, distance):
        pass

    def device_info(self):
        return DeviceInfo(platform="test", width=100, height=100)


def _bot(make_bot, act_results=()):
    bot = make_bot()
    bot._get_adapter = lambda target: _FakeAdapter()
    bot._backend.results.extend(act_results)
    return bot


DONE = {
    "success": True, "finished": True, "actionType": "done",
    "params": {"result": "done", "success": True}, "output": "done",
}


def _messages(caplog):
    return [r.getMessage() for r in caplog.records]


class TestCloseStatusFollowsLastAiOutcome:
    def test_ai_exception_then_close_marks_failed(self, make_bot):
        # Mirrors a script crashing out of ai() (e.g. invalid exclude_tools)
        # with close() left to atexit: the error outcome must survive close().
        bot = _bot(make_bot, [
            {"success": False, "finished": False, "error": "invalid exclude_tools: unknown tool"},
        ])
        with pytest.raises(ActionError):
            bot.ai(object(), "do thing", max_steps=3)
        assert bot._last_ai_status == "error"
        assert bot._last_ai_error == "invalid exclude_tools: unknown tool"
        bot.close()
        assert bot._closed is True
        assert bot._last_ai_status == "error", "close() must not override the error outcome"

    def test_server_terminal_error_marks_failed(self, make_bot):
        # Non-raising error ending (finished error body) counts too.
        bot = _bot(make_bot, [
            {"success": False, "finished": True, "error": "session expired"},
        ])
        result = bot.ai(object(), "do thing", max_steps=3)
        assert result.status == "error"
        assert result.success is False
        assert result.output == "session expired"
        assert bot._last_ai_status == "error"
        assert bot._last_ai_error == "session expired"
        # The failure reason is surfaced as a section banner for the report.
        assert bot._section_errors["do thing"] == "session expired"

    def test_clean_run_completes(self, make_bot):
        bot = _bot(make_bot, [DONE])
        result = bot.ai(object(), "do thing", max_steps=3)
        assert result.success is True
        assert bot._last_ai_status == "completed"
        assert bot._last_ai_error == ""
        # No explicit terminal outcome was recorded; only close() itself latches.
        assert bot._terminalized is False
        bot.close()
        assert bot._terminalized is True

    def test_recovered_error_completes(self, make_bot):
        # An earlier errored ai() followed by a successful one: the run
        # recovered, so the final outcome stays completed.
        bot = _bot(make_bot, [
            {"success": False, "finished": False, "error": "boom"},
            DONE,
        ])
        with pytest.raises(ActionError):
            bot.ai(object(), "first", max_steps=3)
        bot.ai(object(), "second", max_steps=3)
        assert bot._last_ai_status == "completed"
        assert bot._last_ai_error == ""


class TestUserAbortRecordsCancelled:
    """A deliberate user abort (ESC hold, mouse-to-corner failsafe) is a
    cancellation, not a bot failure: the run is recorded with the distinct
    'cancelled' outcome — kept out of the failure bucket — and close() must
    not re-record the run as failed afterwards."""

    def test_failsafe_abort_reports_cancelled_not_failed(self, make_bot, caplog):
        class FailSafeException(Exception):  # matches pyautogui's, by name
            pass

        bot = _bot(make_bot, [
            {"success": True, "finished": False,
             "actionType": "click", "params": {"x": 1, "y": 2}},
        ])

        def corner(adapter, result):
            raise FailSafeException("mouse in a screen corner")

        bot._execute_action = corner
        with caplog.at_level(logging.INFO, logger="qirabot"):
            with pytest.raises(FailSafeException):
                bot.ai(object(), "task", max_steps=3)
            bot.close()

        assert bot._last_ai_status == "cancelled"
        assert "corner" in bot._last_ai_error
        assert bot._user_aborted is True
        assert bot._terminalized is True
        # cancel() won and is idempotent: exactly one cancel record, no fail.
        assert sum("run cancelled" in m for m in _messages(caplog)) == 1
        assert not any("run marked failed" in m for m in _messages(caplog))

    def test_goal_failed_still_completes(self, make_bot):
        # goal_failed means the command ran cleanly but the goal was
        # unreachable; whether that fails the run is the script's call.
        bot = _bot(make_bot, [
            {"success": True, "finished": True, "actionType": "done",
             "params": {"result": "login wall", "success": False},
             "output": "login wall"},
        ])
        result = bot.ai(object(), "do thing", max_steps=3)
        assert result.status == "goal_failed"
        assert result.success is False
        assert bot._last_ai_status == "goal_failed"
        # Not a terminal failure: nothing latched before close().
        assert bot._terminalized is False

    def test_explicit_fail_wins_over_auto_status(self, make_bot, caplog):
        bot = _bot(make_bot, [
            {"success": False, "finished": False, "error": "boom"},
        ])
        with pytest.raises(ActionError):
            bot.ai(object(), "do thing", max_steps=3)
        with caplog.at_level(logging.INFO, logger="qirabot"):
            bot.fail("my own message")
            bot.cancel("a later cancel must not override")
            bot.fail("a second fail must not re-record")
            bot.close()

        assert bot._terminalized is True
        failed = [m for m in _messages(caplog) if "run marked failed" in m]
        assert len(failed) == 1, "fail() is first-wins idempotent"
        assert "my own message" in failed[0]
        assert not any("run cancelled" in m for m in _messages(caplog))


class TestContextManagerExit:
    """__exit__ maps the escape route to the terminal outcome: Ctrl+C is a
    deliberate cancel, any other exception is a failure; close() always runs."""

    def test_keyboard_interrupt_maps_to_cancel(self, make_bot, caplog):
        bot = make_bot()
        with caplog.at_level(logging.INFO, logger="qirabot"):
            with pytest.raises(KeyboardInterrupt):
                with bot:
                    raise KeyboardInterrupt()
        assert bot._closed is True
        assert bot._terminalized is True
        assert any("run cancelled: aborted by user" in m for m in _messages(caplog))
        assert not any("run marked failed" in m for m in _messages(caplog))

    def test_generic_exception_maps_to_fail(self, make_bot, caplog):
        bot = make_bot()
        with caplog.at_level(logging.INFO, logger="qirabot"):
            with pytest.raises(RuntimeError):
                with bot:
                    raise RuntimeError("boom")
        assert bot._closed is True
        assert bot._terminalized is True
        assert bot._last_ai_error == "boom"  # fail(str(exc))
        assert any("run marked failed: boom" in m for m in _messages(caplog))
        assert not any("run cancelled" in m for m in _messages(caplog))
