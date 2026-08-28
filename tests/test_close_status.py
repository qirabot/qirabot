"""Terminal-outcome records around close().

A run's ending lives in two places, and close() must disturb neither:

- the timeline's per-section outcomes/errors — written by ai() as each run
  ends, rendered by the report as badges/banners;
- the run log — fail()/cancel() record the script's own verdict there,
  first-wins idempotent; a closed run can't gain an outcome after the fact.

Includes the atexit shape: a script that crashes out of ai() without calling
fail() still has the error outcome on the timeline when close() runs late.
"""

import logging

import pytest

from qirabot.adapters.base import DeviceAdapter, DeviceInfo
from qirabot.exceptions import ActionError, QirabotError


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


class TestSectionOutcomeSurvivesClose:
    def test_ai_exception_then_close_keeps_error_outcome(self, make_bot):
        # Mirrors a script crashing out of ai() (e.g. invalid exclude_tools)
        # with close() left to atexit: the error outcome and its banner text
        # are already on the timeline and close() must not touch them.
        bot = _bot(make_bot, [
            {"success": False, "finished": False, "error": "invalid exclude_tools: unknown tool"},
        ])
        with pytest.raises(ActionError):
            bot.ai(object(), "do thing", max_steps=3)
        assert bot._timeline.section_outcomes["do thing"] == "error"
        assert bot._timeline.section_errors["do thing"] == "invalid exclude_tools: unknown tool"
        bot.close()
        assert bot._timeline.section_outcomes["do thing"] == "error"
        assert bot._timeline.section_errors["do thing"] == "invalid exclude_tools: unknown tool"

    def test_clean_run_completes_and_close_latches(self, make_bot, caplog):
        bot = _bot(make_bot, [DONE])
        result = bot.ai(object(), "do thing", max_steps=3)
        assert result.success is True
        assert bot._timeline.section_outcomes["do thing"] == "completed"
        bot.close()
        # The run is over: a late fail() (e.g. an outer exception handler
        # firing during interpreter shutdown) must not log an outcome for it.
        with caplog.at_level(logging.INFO, logger="qirabot"):
            bot.fail("late")
        assert not any("run marked failed" in m for m in _messages(caplog))

    def test_recovered_error_keeps_both_section_outcomes(self, make_bot):
        # An errored ai() followed by a successful one: each run keeps its own
        # outcome under its own section key — recovery doesn't rewrite history.
        bot = _bot(make_bot, [
            {"success": False, "finished": False, "error": "boom"},
            DONE,
        ])
        with pytest.raises(ActionError):
            bot.ai(object(), "first", max_steps=3)
        bot.ai(object(), "second", max_steps=3)
        assert bot._timeline.section_outcomes["first"] == "error"
        assert bot._timeline.section_outcomes["second"] == "completed"


class TestUserAbortRecordsCancelled:
    """A deliberate user abort (ESC hold, mouse-to-corner failsafe) is a
    cancellation, not a bot failure: the section is recorded with the distinct
    'cancelled' outcome — kept out of the failure bucket — and the run log
    says cancelled, never failed."""

    def test_failsafe_abort_reports_cancelled_not_failed(self, make_bot, caplog):
        class FailSafeException(Exception):  # matches pyautogui's, by name
            pass

        bot = _bot(make_bot, [
            {"success": True, "finished": False,
             "actionType": "click", "params": {"x": 1, "y": 2}},
        ])

        def corner(adapter, action_type, params):
            raise FailSafeException("mouse in a screen corner")

        bot._execute_action = corner
        with caplog.at_level(logging.INFO, logger="qirabot"):
            with pytest.raises(FailSafeException):
                bot.ai(object(), "task", max_steps=3)
            bot.close()

        assert bot._timeline.section_outcomes["task"] == "cancelled"
        # cancel() won and is idempotent: exactly one cancel record, no fail.
        assert sum("run cancelled" in m for m in _messages(caplog)) == 1
        assert not any("run marked failed" in m for m in _messages(caplog))

    def test_abort_is_sticky_until_cleared(self, make_bot):
        # The abort latches the whole client, not just the interrupted run: a
        # try/except around ai() must not re-take the machine the user just
        # reclaimed.
        class FailSafeException(Exception):
            pass

        bot = _bot(make_bot, [
            {"success": True, "finished": False,
             "actionType": "click", "params": {"x": 1, "y": 2}},
        ])

        def corner(adapter, action_type, params):
            raise FailSafeException("mouse in a screen corner")

        bot._execute_action = corner
        with pytest.raises(FailSafeException):
            bot.ai(object(), "task", max_steps=3)
        with pytest.raises(QirabotError) as excinfo:
            bot.ai(object(), "next task", max_steps=3)
        assert getattr(excinfo.value, "code", "") == "user_abort"
        bot.clear_user_abort()
        bot._backend.results.append(DONE)
        assert bot.ai(object(), "after clearing", max_steps=3).success is True

    def test_goal_failed_is_not_terminal_for_the_run(self, make_bot, caplog):
        # goal_failed means the command ran cleanly but the goal was
        # unreachable; whether that fails the run is the script's call — so
        # an explicit fail() afterwards still goes on record.
        bot = _bot(make_bot, [
            {"success": True, "finished": True, "actionType": "done",
             "params": {"result": "login wall", "success": False},
             "output": "login wall"},
        ])
        result = bot.ai(object(), "do thing", max_steps=3)
        assert result.status == "goal_failed"
        assert result.success is False
        assert bot._timeline.section_outcomes["do thing"] == "goal_failed"
        with caplog.at_level(logging.INFO, logger="qirabot"):
            bot.fail("login wall means this task failed")
        assert any("run marked failed: login wall" in m for m in _messages(caplog))

    def test_explicit_fail_is_first_wins(self, make_bot, caplog):
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

        failed = [m for m in _messages(caplog) if "run marked failed" in m]
        assert len(failed) == 1, "fail() is first-wins idempotent"
        assert "my own message" in failed[0]
        assert not any("run cancelled" in m for m in _messages(caplog))


class TestContextManagerExit:
    """__exit__ maps the escape route to the run-log record: Ctrl+C is a
    deliberate cancel, any other exception is a failure; close() always runs."""

    def test_keyboard_interrupt_maps_to_cancel(self, make_bot, caplog):
        bot = make_bot()
        with caplog.at_level(logging.INFO, logger="qirabot"):
            with pytest.raises(KeyboardInterrupt):
                with bot:
                    raise KeyboardInterrupt()
        assert bot._backend.closed is True
        assert any("run cancelled: aborted by user" in m for m in _messages(caplog))
        assert not any("run marked failed" in m for m in _messages(caplog))

    def test_generic_exception_maps_to_fail(self, make_bot, caplog):
        bot = make_bot()
        with caplog.at_level(logging.INFO, logger="qirabot"):
            with pytest.raises(RuntimeError):
                with bot:
                    raise RuntimeError("boom")
        assert bot._backend.closed is True
        assert any("run marked failed: boom" in m for m in _messages(caplog))
        assert not any("run cancelled" in m for m in _messages(caplog))
