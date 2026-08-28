"""Tests for Qirabot client, StepResult, and RunResult.

v3: the decision engine runs in-process (qirabot.engine); there is no HTTP
transport. conftest's autouse fixture patches ``qirabot.client.LocalBackend``
with :class:`FakeBackend`, so plain ``Qirabot()`` constructs without GCP
credentials; tests script decisions via ``bot._backend.results`` and assert
outbound request bodies via ``bot._backend.requests``.
"""

import logging
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.conftest import FakeBackend
from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig
from qirabot._annotate import render_step_images as _render_step_images
from qirabot.client import (
    ExtractResult,
    LocateResult,
    Qirabot,
    StepResult,
    RunResult,
    VerifyResult,
    _SingleAction,
)
from qirabot.engine.session import StepOutcome
from qirabot.engine.types import TokenUsage
from qirabot.exceptions import ActionError, AuthenticationError


class _SettleFakeAdapter(DeviceAdapter):
    """Minimal adapter with a non-zero settle default, for client wiring tests."""

    _SETTLE_SECONDS = 0.6

    def __init__(self):
        pass

    @classmethod
    def accepts(cls, target):
        return False

    def screenshot(self, config=None):
        return b""

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


class TestStepResult:
    def test_from_outcome(self):
        outcome = StepOutcome(
            action_type="click",
            params={"x": 100, "y": 200},
            output="clicked",
            finished=False,
            decision="clicking the button",
            token_usage=TokenUsage(input_tokens=500, output_tokens=50),
        )
        s = StepResult.from_outcome(outcome, step=3)
        assert s.step == 3
        assert s.action_type == "click"
        assert s.params == {"x": 100, "y": 200}
        assert s.output == "clicked"
        assert s.finished is False
        assert s.decision == "clicking the button"
        assert s.input_tokens == 500
        assert s.output_tokens == 50

    def test_from_outcome_empty(self):
        s = StepResult.from_outcome(StepOutcome(), step=1)
        assert s.step == 1
        assert s.action_type == ""
        assert s.params == {}
        assert s.output == ""
        assert s.finished is False

    def test_from_outcome_finished(self):
        s = StepResult.from_outcome(StepOutcome(finished=True, output="done"), step=5)
        assert s.finished is True
        assert s.output == "done"


class TestVerifyResult:
    def test_passed(self):
        r = VerifyResult(
            passed=True,
            reason="the banner is visible",
            input_tokens=500,
            output_tokens=60,
            thinking_tokens=20,
        )
        assert r.passed is True
        assert r.reason == "the banner is visible"
        assert r.input_tokens == 500
        assert r.output_tokens == 60
        assert r.thinking_tokens == 20

    def test_bool_truthy_when_passed(self):
        assert bool(VerifyResult(passed=True)) is True
        assert VerifyResult(passed=True)  # usable in if/assert

    def test_bool_falsy_when_failed(self):
        r = VerifyResult(passed=False, reason="no banner")
        assert bool(r) is False
        assert not r
        assert r.reason == "no banner"  # reason still readable on a failed check


class TestExtractResult:
    def test_is_str_subclass(self):
        r = ExtractResult("hello", input_tokens=10, output_tokens=5)
        assert isinstance(r, str)
        assert r == "hello"
        assert r.upper() == "HELLO"
        assert r + "!" == "hello!"

    def test_carries_tokens(self):
        r = ExtractResult("42", input_tokens=700, output_tokens=30, thinking_tokens=8)
        assert r.input_tokens == 700
        assert r.output_tokens == 30
        assert r.thinking_tokens == 8
        assert float(r) == 42.0

    def test_empty(self):
        r = ExtractResult("")
        assert r == ""
        assert r.input_tokens == 0
        assert r.output_tokens == 0


class TestLocateResult:
    def test_tuple_unpacking(self):
        x, y = LocateResult(x=10, y=20)
        assert (x, y) == (10, 20)

    def test_carries_tokens(self):
        r = LocateResult(x=1, y=2, input_tokens=10, output_tokens=5, thinking_tokens=2)
        assert (r.x, r.y) == (1, 2)
        assert r.input_tokens == 10
        assert r.output_tokens == 5
        assert r.thinking_tokens == 2


class TestQirabotLocate:
    def _make_mocked_bot(self, make_bot):
        bot = make_bot()
        bot._get_adapter = MagicMock(return_value=MagicMock())
        bot._ai_action = MagicMock(return_value=_SingleAction(
            action_type="locate",
            params={"locate": "OK button", "x": 100, "y": 200},
            finished=True,
        ))
        return bot

    def test_builds_locate_action_and_skips_execution(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        bot.locate("target", "OK button")
        ca = bot._ai_action.call_args
        action = ca.kwargs.get("action") or ca[1].get("action") or ca[0][1]
        assert action["type"] == "locate"
        assert action["params"] == {"locate": "OK button"}
        assert ca.kwargs.get("execute_result") is False

    def test_returns_coords(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        r = bot.locate("target", "OK button")
        assert isinstance(r, LocateResult)
        assert (r.x, r.y) == (100, 200)
        x, y = bot.locate("target", "OK button")
        assert (x, y) == (100, 200)

    def test_timeout_polls_wait_for(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        bot.wait_for = MagicMock()
        bot.locate("target", "OK button", timeout=5, interval=1)
        assert bot.wait_for.call_count == 1

    def test_no_timeout_skips_wait_for(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        bot.wait_for = MagicMock()
        bot.locate("target", "OK button")
        bot.wait_for.assert_not_called()


class TestRunResult:
    def test_success(self):
        r = RunResult(success=True, output="completed", steps=[])
        assert r.success is True
        assert r.output == "completed"

    def test_failure(self):
        r = RunResult(success=False, output="max steps reached")
        assert r.success is False
        assert r.steps == []


class TestUserAbortViaFailSafe:
    """pyautogui's corner kill switch (FailSafeException) is a USER abort: it
    must end the run, not be fed back to the model as a recoverable action
    error — the model's recovery move is exactly what would defeat it."""

    def _bot(self, make_bot):
        bot = make_bot()
        bot._get_adapter = lambda target: _SettleFakeAdapter()
        return bot

    def test_failsafe_exception_ends_the_run(self, make_bot):
        class FailSafeException(Exception):  # matches pyautogui's, by name
            pass

        bot = self._bot(make_bot)
        bot._backend.results.append({
            "success": True, "finished": False,
            "actionType": "click", "params": {"x": 1, "y": 2},
        })

        def corner(adapter, action_type, params):
            raise FailSafeException("mouse in a screen corner")

        bot._execute_action = corner
        with pytest.raises(FailSafeException):
            bot.ai(object(), "task", max_steps=3)

    def test_ordinary_action_errors_still_recover(self, make_bot):
        # Regression guard for the feed-back-and-continue contract: only the
        # failsafe bypasses it, everything else still lets the model retry.
        bot = self._bot(make_bot)
        bot._backend.results.extend([
            {"success": True, "finished": False,
             "actionType": "click", "params": {"x": 1, "y": 2}},
            {"success": True, "finished": True, "actionType": "done",
             "params": {"result": "ok", "success": True}, "output": "ok"},
        ])

        def flaky(adapter, action_type, params):
            if action_type == "click":
                raise ValueError("transient")

        bot._execute_action = flaky
        result = bot.ai(object(), "task", max_steps=3)
        assert result.success is True  # the loop survived the failed action
        # The failure was fed back to the model on the next step's request.
        _, second_request = bot._backend.requests[1]
        assert second_request["action_result"] == "ERROR: transient"


class TestAiLoopFinish:
    """The done action's success flag drives RunResult.success: a clean run that
    concludes the goal is unreachable (success:false) is a failure, not a pass.
    The top-level success only means the step committed, so it stays true here."""

    def _bot_returning(self, make_bot, done_result):
        bot = make_bot()
        bot._get_adapter = lambda target: _SettleFakeAdapter()
        bot._backend.results.append(done_result)
        return bot

    def test_done_success_true_yields_success(self, make_bot):
        bot = self._bot_returning(make_bot, {
            "success": True, "finished": True, "actionType": "done",
            "params": {"result": "all good", "success": True},
            "output": "all good",
        })
        result = bot.ai(object(), "do thing", max_steps=3)
        assert result.success is True
        assert result.output == "all good"
        assert result.status == "completed"

    def test_done_success_false_yields_failure(self, make_bot):
        bot = self._bot_returning(make_bot, {
            "success": True, "finished": True, "actionType": "done",
            "params": {"result": "blocked: login wall", "success": False},
            "output": "blocked: login wall",
        })
        result = bot.ai(object(), "do thing", max_steps=3)
        assert result.success is False
        assert result.output == "blocked: login wall"
        assert result.status == "goal_failed"

    def test_done_missing_flag_defaults_success(self, make_bot):
        bot = self._bot_returning(make_bot, {
            "success": True, "finished": True, "actionType": "done",
            "params": {"result": "legacy"},
            "output": "legacy",
        })
        result = bot.ai(object(), "do thing", max_steps=3)
        assert result.success is True
        assert result.status == "completed"


class TestAiLoopStatus:
    """Each of ai()'s terminal paths reports a distinct RunResult.status, and
    the section outcome recorded for the report carries the same value —
    success stays a two-state bool (True only for "completed")."""

    def _bot_returning(self, make_bot, act_result, repeat=1):
        bot = make_bot()
        bot._get_adapter = lambda target: _SettleFakeAdapter()
        bot._execute_action = lambda *a, **k: None
        bot._backend.results.extend(dict(act_result) for _ in range(repeat))
        return bot

    def test_step_error_raises_and_keeps_error_text(self, make_bot):
        # Engine step failures raise ActionError; ai() records the "error"
        # outcome and keeps the message for the report's section banner.
        bot = self._bot_returning(make_bot, {
            "success": False, "finished": False, "error": "decide exploded",
        })
        with pytest.raises(ActionError, match="decide exploded"):
            bot.ai(object(), "do thing", max_steps=3)
        assert bot._timeline.section_outcomes["do thing"] == "error"
        assert "decide exploded" in bot._timeline.section_errors["do thing"]

    def test_max_steps_yields_max_steps(self, make_bot):
        # Never finishes: every step is a successful non-terminal action.
        bot = self._bot_returning(make_bot, {
            "success": True, "finished": False, "actionType": "wait", "params": {},
        }, repeat=2)
        result = bot.ai(object(), "do thing", max_steps=2)
        assert result.success is False
        assert result.status == "max_steps"
        assert result.output == "max steps reached"
        assert len(result.steps) == 2
        assert bot._timeline.section_outcomes["do thing"] == "max_steps"

    def test_non_terminal_error_raises_and_records_error(self, make_bot):
        bot = self._bot_returning(make_bot, {
            "success": False, "finished": False, "error": "element not found",
        })
        with pytest.raises(ActionError):
            bot.ai(object(), "do thing", max_steps=3)
        assert bot._timeline.section_outcomes["do thing"] == "error"

    def test_max_steps_records_section_error_not_step(self, make_bot):
        # A max-steps ending is a section-level banner, NOT a synthetic step
        # entry — synthetic steps made the report's step count disagree with
        # the engine's. One real step ran, so exactly one step is recorded.
        bot = self._bot_returning(make_bot, {
            "success": True, "finished": False, "actionType": "wait", "params": {},
        })
        recorded = []
        bot._record_step = lambda *a, **k: recorded.append(k) or None
        bot.ai(object(), "do thing", max_steps=1)
        assert len(recorded) == 1
        assert bot._timeline.section_errors["do thing"] == "max steps reached (1)"

    def test_repeat_instruction_gets_numbered_section_key(self, make_bot):
        # Two ai() runs with the same instruction must NOT share a section
        # key — the second run's outcome/error would overwrite the first's
        # in the report. Repeats get "<instruction> #2", "#3", ...
        bot = self._bot_returning(make_bot, {
            "success": True, "finished": True, "actionType": "done",
            "params": {}, "output": "ok",
        }, repeat=3)
        sections = []
        bot._record_step = (
            lambda *a, **k: sections.append(bot._timeline.current_section) or None
        )
        bot.ai(object(), "do thing", max_steps=3)
        bot.ai(object(), "do thing", max_steps=3)
        bot.ai(object(), "other", max_steps=3)
        assert set(bot._timeline.section_outcomes) == {"do thing", "do thing #2", "other"}
        assert bot._timeline.section_outcomes["do thing"] == "completed"
        assert bot._timeline.section_outcomes["do thing #2"] == "completed"
        # Log entries carry the numbered key too, so the report renders the
        # runs as separate sections.
        assert sections == ["do thing", "do thing #2", "other"]
        # Section is restored between runs, so standalone actions afterwards
        # still land in "setup".
        assert bot._timeline.current_section == "setup"

    def test_repeat_instruction_outcomes_stay_separate(self, make_bot):
        # First run truncates at max steps, second hits a step error: each
        # run's outcome must survive under its own key.
        bot = self._bot_returning(make_bot, {
            "success": True, "finished": False, "actionType": "wait", "params": {},
        })
        bot.ai(object(), "do thing", max_steps=1)
        bot._backend.results.append({
            "success": False, "finished": False, "error": "decide exploded",
        })
        with pytest.raises(ActionError):
            bot.ai(object(), "do thing", max_steps=1)
        assert bot._timeline.section_errors["do thing"] == "max steps reached (1)"
        assert bot._timeline.section_outcomes["do thing"] == "max_steps"
        assert bot._timeline.section_outcomes["do thing #2"] == "error"

    def test_step_error_records_no_step(self, make_bot):
        # A failed step committed nothing engine-side, so none is recorded
        # locally either — no synthetic step entries in the report.
        bot = self._bot_returning(make_bot, {
            "success": False, "finished": False, "error": "decide exploded",
        })
        recorded = []
        bot._record_step = lambda *a, **k: recorded.append(k) or None
        with pytest.raises(ActionError):
            bot.ai(object(), "do thing", max_steps=3)
        assert recorded == []


class TestSessionUsage:
    """bot.usage: the public session-wide token/step totals snapshot."""

    def _bot(self, make_bot):
        bot = make_bot()
        bot._get_adapter = lambda target: _SettleFakeAdapter()
        bot._execute_action = lambda *a, **k: None
        return bot

    def test_starts_at_zero(self, make_bot):
        u = make_bot().usage
        assert u.ai_steps == 0
        assert u.total_tokens == 0

    def test_ai_run_accumulates(self, make_bot):
        bot = self._bot(make_bot)
        bot._backend.results.extend([
            {
                "success": True, "finished": False, "actionType": "click",
                "params": {}, "inputTokens": 100, "outputTokens": 20,
                "thinkingTokens": 5,
            },
            {
                "success": True, "finished": True, "actionType": "done",
                "params": {"result": "ok", "success": True}, "output": "ok",
                "inputTokens": 200, "outputTokens": 30, "thinkingTokens": 10,
            },
        ])
        bot.ai(object(), "task", max_steps=3)
        u = bot.usage
        assert u.ai_steps == 2
        assert u.input_tokens == 300
        assert u.output_tokens == 50
        assert u.thinking_tokens == 15
        # thinking is already inside output (Anthropic semantics):
        # total is input + output, thinking not added again.
        assert u.total_tokens == 350

    def test_standalone_verify_counts(self, make_bot):
        bot = self._bot(make_bot)
        bot._backend.results.append({
            "success": True, "finished": True, "actionType": "done",
            "output": "banner visible", "inputTokens": 500, "outputTokens": 60,
            "thinkingTokens": 20,
        })
        bot.verify(object(), "the banner is visible")
        u = bot.usage
        assert u.ai_steps == 1
        assert u.total_tokens == 560

    def test_ai_located_action_counts(self, make_bot):
        # click()/type_text()/… discard their /act result — the usage must
        # still land in the session totals (pure bolt-on scripts would
        # otherwise report ~0 tokens).
        bot = self._bot(make_bot)
        bot._backend.results.append({
            "success": True, "finished": True, "actionType": "click",
            "params": {"x": 10, "y": 20}, "inputTokens": 400, "outputTokens": 30,
        })
        bot.click(object(), "Login button")
        u = bot.usage
        assert u.ai_steps == 1
        assert u.total_tokens == 430

    def test_cache_tokens_join_the_totals(self, make_bot):
        # input_tokens is the non-cached prompt only (both providers); the
        # cached portion must reach the totals or Claude runs (cache_control
        # on every step) understate their spend by the whole prompt.
        bot = self._bot(make_bot)
        bot._backend.results.append({
            "success": True, "finished": True, "actionType": "done",
            "params": {"result": "ok", "success": True}, "output": "ok",
            "inputTokens": 100, "outputTokens": 50,
            "cacheReadTokens": 9_000, "cacheWriteTokens": 1_000,
        })
        bot.ai(object(), "task", max_steps=2)
        u = bot.usage
        assert u.cache_read_tokens == 9_000
        assert u.cache_write_tokens == 1_000
        assert u.total_tokens == 100 + 9_000 + 1_000 + 50

    def test_failed_locate_keeps_tokens_but_not_the_step(self, make_bot):
        # The engine reports the failed attempt's usage on the outcome;
        # the spend counts, the step does not (none committed).
        bot = self._bot(make_bot)
        bot._backend.results.append({
            "success": False, "error": "element not found: anything",
            "inputTokens": 900, "outputTokens": 45,
        })
        with pytest.raises(ActionError):
            bot.click(object(), "anything", retry=0)
        u = bot.usage
        assert u.ai_steps == 0
        assert u.input_tokens == 900
        assert u.output_tokens == 45

    def test_failed_ai_step_keeps_tokens(self, make_bot):
        bot = self._bot(make_bot)
        bot._backend.results.append({
            "success": False, "finished": False, "error": "decide exploded",
            "inputTokens": 700, "outputTokens": 20,
        })
        with pytest.raises(ActionError):
            bot.ai(object(), "task", max_steps=3)
        u = bot.usage
        assert u.ai_steps == 0
        assert u.total_tokens == 720

    def test_snapshot_is_frozen_and_does_not_track(self, make_bot):
        bot = self._bot(make_bot)
        before = bot.usage
        with pytest.raises(Exception):  # dataclasses.FrozenInstanceError
            before.input_tokens = 999  # type: ignore[misc]
        bot._backend.results.append({
            "success": True, "finished": True, "actionType": "done",
            "output": "yes", "inputTokens": 10, "outputTokens": 2,
        })
        bot.verify(object(), "anything")
        assert before.ai_steps == 0  # old snapshot untouched
        assert bot.usage.ai_steps == 1


class TestQirabotInit:
    def test_task_id_is_locally_generated(self, make_bot):
        # No server round-trip anymore: the run id is minted locally — bare
        # hex, since there is no longer a cloud id to tell it apart from.
        bot = make_bot()
        assert len(bot.task_id) == 8
        int(bot.task_id, 16)  # hex

    def test_task_ids_are_unique_per_instance(self, make_bot):
        assert make_bot().task_id != make_bot().task_id

    def test_report_dir_is_named_for_the_whole_task_id(self, make_bot):
        # The console prints the run id and never the path, so the directory
        # has to carry the id intact for the two to be matched up. Truncating
        # it also threw away the uniqueness two same-second clients rely on
        # to stay out of each other's output dir.
        bot = make_bot()
        assert Path(bot.report_dir).name.endswith(f"-{bot.task_id}")
        assert Path(bot.report_dir).parent.name == time.strftime("%Y-%m-%d")

    def test_backend_receives_model_config(self, make_bot):
        bot = make_bot(
            model="gemini-vertex/gemini-3-flash-preview",
            vertex_project="proj",
            vertex_location="us-central1",
        )
        kwargs = bot._backend.init_kwargs
        assert kwargs["model"] == "gemini-vertex/gemini-3-flash-preview"
        assert kwargs["vertex_project"] == "proj"
        assert kwargs["vertex_location"] == "us-central1"

    def test_media_resolution_param_passthrough(self, make_bot):
        bot = make_bot(media_resolution="medium")
        assert bot._backend.init_kwargs["media_resolution"] == "medium"

    def test_media_resolution_from_env(self, monkeypatch, make_bot):
        monkeypatch.setenv("QIRA_MEDIA_RESOLUTION", "low")
        bot = make_bot()
        assert bot._backend.init_kwargs["media_resolution"] == "low"

    def test_media_resolution_param_overrides_env(self, monkeypatch, make_bot):
        monkeypatch.setenv("QIRA_MEDIA_RESOLUTION", "low")
        bot = make_bot(media_resolution="ultra_high")
        assert bot._backend.init_kwargs["media_resolution"] == "ultra_high"

    def test_report_dir_root_from_env(self, monkeypatch):
        monkeypatch.setenv("QIRA_REPORT_DIR", "/tmp/shots")
        bot = Qirabot(report=False)
        # env sets only the root; date/run subdirs are appended automatically.
        # Compare via Path ancestry so the assertion is OS-separator agnostic.
        assert Path("/tmp/shots") in bot._report_dir.parents
        bot.close()

    def test_report_dir_param_overrides_env(self, monkeypatch, make_bot):
        monkeypatch.setenv("QIRA_REPORT_DIR", "/tmp/shots")
        bot = make_bot(report_dir="./local")
        assert Path("local") in bot._report_dir.parents

    def test_report_dir_expands_tilde(self, monkeypatch, make_bot):
        # A ~ root (e.g. QIRA_REPORT_DIR=~/reports in a .env, which no shell
        # expands) must resolve to home, not a literal "~" directory under cwd.
        bot = make_bot(report_dir="~/qira_reports")
        assert Path("~/qira_reports").expanduser() in bot._report_dir.parents
        assert "~" not in str(bot._report_dir)

    def test_settle_seconds_default_none(self, make_bot):
        bot = make_bot()
        assert bot._settle_seconds is None

    def test_settle_seconds_param(self, make_bot):
        bot = make_bot(settle_seconds=0.3)
        assert bot._settle_seconds == 0.3

    def test_settle_seconds_zero_allowed(self, make_bot):
        bot = make_bot(settle_seconds=0)
        assert bot._settle_seconds == 0

    def test_settle_seconds_negative_rejected(self, make_bot):
        with pytest.raises(ValueError, match="settle_seconds must be >= 0"):
            make_bot(settle_seconds=-1)

    def test_settle_seconds_from_env(self, monkeypatch, make_bot):
        monkeypatch.setenv("QIRA_SETTLE_SECONDS", "1.5")
        bot = make_bot()
        assert bot._settle_seconds == 1.5

    def test_settle_seconds_param_overrides_env(self, monkeypatch, make_bot):
        monkeypatch.setenv("QIRA_SETTLE_SECONDS", "1.5")
        bot = make_bot(settle_seconds=0.2)
        assert bot._settle_seconds == 0.2

    def test_settle_seconds_env_invalid_rejected(self, monkeypatch, make_bot):
        monkeypatch.setenv("QIRA_SETTLE_SECONDS", "soon")
        with pytest.raises(ValueError, match="QIRA_SETTLE_SECONDS must be a number"):
            make_bot()

    def test_settle_seconds_applied_to_adapter(self, monkeypatch, make_bot):
        import qirabot.client as client_mod

        adapter = _SettleFakeAdapter()
        monkeypatch.setattr(client_mod.auto, "detect", lambda target: adapter)
        bot = make_bot(settle_seconds=0.25)
        got = bot._get_adapter(object())
        assert got is adapter
        assert adapter.settle_seconds == 0.25

    def test_settle_seconds_none_keeps_adapter_default(self, monkeypatch, make_bot):
        import qirabot.client as client_mod

        adapter = _SettleFakeAdapter()
        monkeypatch.setattr(client_mod.auto, "detect", lambda target: adapter)
        bot = make_bot()
        bot._get_adapter(object())
        assert adapter._settle_override is None
        assert adapter.settle_seconds == adapter._SETTLE_SECONDS


class TestCloudRemovedMigrationGuard:
    """A stale v2 setup (QIRA_API_KEY set, no v3 model configured) must fail
    at construction with a migration pointer instead of the old variable
    being silently ignored. The guard fires *after* the provider handshake so
    the message reflects the real credential state: working ADC points at the
    leftover key, broken ADC keeps the full gcloud setup guidance."""

    def test_stale_api_key_without_model_raises(self, monkeypatch):
        monkeypatch.setenv("QIRA_API_KEY", "stale_v2_key")
        with pytest.raises(AuthenticationError) as exc_info:
            Qirabot(report=False)
        assert exc_info.value.code == "auth.cloud_removed"

    def test_working_adc_message_points_at_the_key_not_adc(self, monkeypatch):
        # Backend construction succeeds (conftest's FakeBackend): the user's
        # GCP setup is fine, so the message must say so and direct them at
        # the leftover key — not tell them to configure ADC again.
        monkeypatch.setenv("QIRA_API_KEY", "stale_v2_key")
        with pytest.raises(AuthenticationError) as exc_info:
            Qirabot(report=False)
        msg = str(exc_info.value)
        assert "Google Cloud setup works" in msg
        assert "remove QIRA_API_KEY" in msg
        assert "gcloud auth application-default login" not in msg

    def test_working_adc_backend_is_closed_before_raising(self, monkeypatch):
        import qirabot.client as client_mod

        monkeypatch.setenv("QIRA_API_KEY", "stale_v2_key")
        built = []
        monkeypatch.setattr(
            client_mod,
            "LocalBackend",
            lambda **kw: built.append(FakeBackend(**kw)) or built[-1],
        )
        with pytest.raises(AuthenticationError):
            Qirabot(report=False)
        assert built and built[0].closed

    def test_broken_adc_message_keeps_setup_guidance(self, monkeypatch):
        import qirabot.client as client_mod
        from qirabot.engine.providers.base import ProviderError

        def _no_adc(**kw):
            raise ProviderError("vertex", "could not resolve ADC credentials")

        monkeypatch.setenv("QIRA_API_KEY", "stale_v2_key")
        monkeypatch.setattr(client_mod, "LocalBackend", _no_adc)
        with pytest.raises(AuthenticationError) as exc_info:
            Qirabot(report=False)
        assert exc_info.value.code == "auth.cloud_removed"
        msg = str(exc_info.value)
        assert "gcloud auth application-default login" in msg
        assert "could not resolve ADC credentials" in msg

    def test_broken_adc_without_stale_key_stays_credentials_error(self, monkeypatch):
        import qirabot.client as client_mod
        from qirabot.engine.providers.base import ProviderError

        def _no_adc(**kw):
            raise ProviderError("vertex", "could not resolve ADC credentials")

        monkeypatch.setattr(client_mod, "LocalBackend", _no_adc)
        with pytest.raises(AuthenticationError) as exc_info:
            Qirabot(report=False)
        assert exc_info.value.code == "auth.credentials"

    def test_message_names_the_dotenv_file_when_key_came_from_one(
        self, monkeypatch, tmp_path
    ):
        import qirabot._dotenv as dotenv_mod
        from qirabot._dotenv import load_dotenv

        envfile = tmp_path / ".env"
        envfile.write_text("QIRA_API_KEY=stale_v2_key\n", encoding="utf-8")
        monkeypatch.setattr(dotenv_mod, "_injected", {})
        monkeypatch.delenv("QIRA_API_KEY", raising=False)
        load_dotenv(str(envfile))
        monkeypatch.setenv("QIRA_API_KEY", "stale_v2_key")  # register cleanup
        with pytest.raises(AuthenticationError) as exc_info:
            Qirabot(report=False)
        assert f"loaded from {envfile}" in str(exc_info.value)

    def test_explicit_model_arg_disarms_guard(self, monkeypatch, make_bot):
        monkeypatch.setenv("QIRA_API_KEY", "stale_v2_key")
        bot = make_bot(model="gemini-vertex/gemini-3-flash-preview")
        assert bot._backend.init_kwargs["model"] == "gemini-vertex/gemini-3-flash-preview"

    def test_qira_model_env_disarms_guard(self, monkeypatch, make_bot):
        monkeypatch.setenv("QIRA_API_KEY", "stale_v2_key")
        monkeypatch.setenv("QIRA_MODEL", "gemini-vertex/gemini-3-flash-preview")
        bot = make_bot()
        assert bot._backend.init_kwargs["model"] == "gemini-vertex/gemini-3-flash-preview"

    def test_no_api_key_constructs_without_model(self, make_bot):
        # conftest scrubs QIRA_API_KEY: a clean v3 setup needs no model arg
        # (the engine default applies).
        bot = make_bot()
        assert bot._backend.init_kwargs["model"]


class TestQirabotContextManager:
    def test_enter_exit(self, make_bot):
        bot = make_bot()
        with bot as b:
            assert b is bot
        # close should not raise on second call
        bot.close()

    def test_exit_with_exception_records_failure(self, make_bot):
        bot = make_bot()
        bot.fail = MagicMock(wraps=bot.fail)
        with pytest.raises(ValueError):
            with bot:
                raise ValueError("boom")
        # __exit__ routed the exception through fail() and closed the client.
        bot.fail.assert_called_once_with("boom")
        assert bot._closed is True

    def test_exit_with_keyboardinterrupt_records_cancel(self, make_bot):
        bot = make_bot()
        bot.cancel = MagicMock(wraps=bot.cancel)
        with pytest.raises(KeyboardInterrupt):
            with bot:
                raise KeyboardInterrupt()
        # Ctrl+C is a deliberate cancel, not a failure.
        bot.cancel.assert_called_once_with("aborted by user")
        assert bot._closed is True


class TestQirabotTerminalStatus:
    """fail()/cancel() record the script's verdict in the run log — the first
    terminal outcome wins, and a closed run can't gain one after the fact."""

    def _messages(self, caplog):
        return [r.getMessage() for r in caplog.records]

    def test_fail_logs_once_first_message_wins(self, make_bot, caplog):
        bot = make_bot()
        with caplog.at_level(logging.INFO, logger="qirabot"):
            bot.fail("first")
            bot.fail("second")
        failed = [m for m in self._messages(caplog) if "run marked failed" in m]
        assert failed == ["run marked failed: first"]

    def test_cancel_after_fail_is_a_noop(self, make_bot, caplog):
        bot = make_bot()
        with caplog.at_level(logging.INFO, logger="qirabot"):
            bot.fail("boom")
            bot.cancel("late ctrl+c")
        assert any("run marked failed: boom" in m for m in self._messages(caplog))
        assert not any("run cancelled" in m for m in self._messages(caplog))

    def test_fail_after_cancel_is_a_noop(self, make_bot, caplog):
        bot = make_bot()
        with caplog.at_level(logging.INFO, logger="qirabot"):
            bot.cancel("aborted by user")
            bot.fail("late catch-all handler")
        assert any("run cancelled: aborted by user" in m for m in self._messages(caplog))
        assert not any("run marked failed" in m for m in self._messages(caplog))

    def test_fail_after_close_logs_nothing(self, make_bot, caplog):
        bot = make_bot()
        bot.close()
        with caplog.at_level(logging.INFO, logger="qirabot"):
            bot.fail("late")
            bot.cancel("late")
        assert not any("run marked failed" in m for m in self._messages(caplog))
        assert not any("run cancelled" in m for m in self._messages(caplog))

    def test_close_is_local_and_closes_backend(self, make_bot):
        bot = make_bot()
        backend = bot._backend
        bot.close()
        assert bot._closed is True
        assert backend.closed is True

    def test_close_is_idempotent(self, make_bot):
        bot = make_bot()
        bot.close()
        bot.close()
        assert bot._closed is True


class TestQirabotTypeTextParams:
    # type_text() resolves the device adapter before calling _ai_action, so
    # tests that pass a bare string as the target must also mock _get_adapter.

    def _make_mocked_bot(self, make_bot):
        bot = make_bot()
        bot._get_adapter = MagicMock(return_value=MagicMock())
        bot._ai_action = MagicMock(return_value={"success": True})
        return bot

    def test_type_text_builds_action_with_press_enter(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        bot.type_text("target", "field", "hello", press_enter=True)
        call_args = bot._ai_action.call_args
        action = call_args.kwargs.get("action") or call_args[1].get("action") or call_args[0][1]
        assert action["params"]["press_enter"] is True
        assert action["params"]["text"] == "hello"

    def test_type_text_builds_action_with_clear(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        bot.type_text("target", "field", "hello", clear_before_typing=True)
        call_args = bot._ai_action.call_args
        action = call_args.kwargs.get("action") or call_args[1].get("action") or call_args[0][1]
        assert action["params"]["clear_before_typing"] is True

    def test_type_text_omits_false_flags(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        bot.type_text("target", "field", "hello")
        call_args = bot._ai_action.call_args
        action = call_args.kwargs.get("action") or call_args[1].get("action") or call_args[0][1]
        assert "press_enter" not in action["params"]
        assert "clear_before_typing" not in action["params"]


class TestQirabotLongPressParams:
    def _make_mocked_bot(self, make_bot):
        bot = make_bot()
        bot._get_adapter = MagicMock(return_value=MagicMock())
        bot._ai_action = MagicMock(return_value={"success": True})
        return bot

    def test_long_press_omits_default_duration(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        bot.long_press("target", "app icon")
        call_args = bot._ai_action.call_args
        action = call_args.kwargs.get("action") or call_args[1].get("action") or call_args[0][1]
        assert action["type"] == "long_press"
        assert action["params"] == {"locate": "app icon"}

    def test_long_press_converts_seconds_to_ms(self, make_bot):
        bot = self._make_mocked_bot(make_bot)
        bot.long_press("target", "app icon", duration=1.5)
        call_args = bot._ai_action.call_args
        action = call_args.kwargs.get("action") or call_args[1].get("action") or call_args[0][1]
        assert action["params"]["duration"] == 1500


class TestMouseDownUpKeyDownUp:
    """mouse_down/mouse_up(with locate) are AI-located; mouse_up(no locate) and
    key_down/key_up are deterministic client-side actions (no AI, no billing)."""

    def _ai_bot(self, make_bot):
        bot = make_bot()
        bot._get_adapter = MagicMock(return_value=MagicMock())
        bot._ai_action = MagicMock(return_value={"success": True})
        return bot

    def _action_of(self, mock):
        ca = mock.call_args
        return ca.kwargs.get("action") or ca[1].get("action") or ca[0][1]

    def test_mouse_down_is_ai_located(self, make_bot):
        bot = self._ai_bot(make_bot)
        bot.mouse_down("target", "the blue piece")
        action = self._action_of(bot._ai_action)
        assert action["type"] == "mouse_down"
        assert action["params"] == {"locate": "the blue piece"}

    def test_mouse_up_with_locate_is_ai_located(self, make_bot):
        bot = self._ai_bot(make_bot)
        bot.mouse_up("target", "the drop zone")
        action = self._action_of(bot._ai_action)
        assert action["type"] == "mouse_up"
        assert action["params"] == {"locate": "the drop zone"}

    def test_mouse_up_without_locate_is_deterministic(self, make_bot):
        bot = make_bot()
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        bot._adapters[id(target)] = adapter
        bot._ai_action = MagicMock()

        bot.mouse_up(target)  # no locate -> release at current cursor

        adapter.execute.assert_called_once_with("mouse_up", {})
        bot._ai_action.assert_not_called()  # deterministic: no AI, no billing

    def test_key_down_up_are_deterministic(self, make_bot):
        bot = make_bot()
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        bot._adapters[id(target)] = adapter

        bot.key_down(target, "w")
        bot.key_up(target, "w")

        assert adapter.execute.call_args_list[0][0] == ("key_down", {"key": "w"})
        assert adapter.execute.call_args_list[1][0] == ("key_up", {"key": "w"})

    def test_bound_variants_delegate(self, make_bot):
        bot = make_bot()
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        bot._adapters[id(target)] = adapter

        bound = bot.bind(target)
        bound.key_down("shift")
        bound.key_up("shift")
        bound.mouse_up()  # no locate -> deterministic

        types = [c[0][0] for c in adapter.execute.call_args_list]
        assert types == ["key_down", "key_up", "mouse_up"]


class TestAiLoopReleasesHeldInputs:
    """Safety net: after an ai() run the client must release any input the model
    held with mouse_down/key_down but never released — on done, on max-steps, and
    on exception — so a stuck button/key can't outlive the run."""

    def _adapter(self):
        # Real device_info (so the request body serializes) + a release spy +
        # a no-op execute (we only assert the end-of-run release).
        a = _SettleFakeAdapter()
        a.release_all_inputs = MagicMock()
        a.execute = MagicMock()
        return a

    def _bot(self, make_bot, adapter):
        bot = make_bot()
        bot._get_adapter = lambda target: adapter
        return bot

    def test_released_on_done(self, make_bot):
        adapter = self._adapter()
        bot = self._bot(make_bot, adapter)
        bot._backend.results.append({
            "success": True, "finished": True, "actionType": "done",
            "params": {"result": "ok", "success": True}, "output": "ok",
        })
        bot.ai(object(), "hold then finish", max_steps=3)
        adapter.release_all_inputs.assert_called_once()

    def test_released_on_max_steps(self, make_bot):
        adapter = self._adapter()
        bot = self._bot(make_bot, adapter)
        # Never finishes -> loop exhausts max_steps with a key still "held".
        bot._backend.results.extend(
            {"success": True, "finished": False, "actionType": "key_down",
             "params": {"key": "w"}}
            for _ in range(2)
        )
        bot.ai(object(), "hold forever", max_steps=2)
        adapter.release_all_inputs.assert_called_once()

    def test_released_on_exception(self, make_bot):
        adapter = self._adapter()
        bot = self._bot(make_bot, adapter)
        bot._backend.results.append(RuntimeError("network down"))
        with pytest.raises(RuntimeError, match="network down"):
            bot.ai(object(), "will crash", max_steps=2)
        adapter.release_all_inputs.assert_called_once()


class TestPressKey:
    """press_key is a deterministic (no-AI) action: it routes through
    adapter.execute (reusing tab-switch + settle) and returns the current
    target so a tab-opening combo like ctrl+t is followed."""

    def test_executes_action_and_returns_current_target(self, make_bot):
        bot = make_bot()
        target, switched = object(), object()  # e.g. ctrl+t opens a new tab
        adapter = MagicMock()
        adapter.current_target = switched
        bot._adapters[id(target)] = adapter

        out = bot.press_key(target, "ctrl+t")

        adapter.execute.assert_called_once_with("press_key", {"key": "ctrl+t"})
        assert out is switched

    def test_bound_press_key_delegates(self, make_bot):
        bot = make_bot()
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        bot._adapters[id(target)] = adapter

        bot.bind(target).press_key("Enter")

        adapter.execute.assert_called_once_with("press_key", {"key": "Enter"})

    def test_duration_included_only_when_positive(self, make_bot):
        # The wire params (and hence the recorded local step) must carry
        # duration_seconds only for an actual hold, so a plain tap keeps the
        # exact legacy shape old report consumers expect.
        bot = make_bot()
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        bot._adapters[id(target)] = adapter

        bot.press_key(target, "w", duration_seconds=2)
        adapter.execute.assert_called_once_with(
            "press_key", {"key": "w", "duration_seconds": 2}
        )

        adapter.execute.reset_mock()
        bot.press_key(target, "w")
        adapter.execute.assert_called_once_with("press_key", {"key": "w"})

    def test_bound_press_key_passes_duration(self, make_bot):
        bot = make_bot()
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        bot._adapters[id(target)] = adapter

        bot.bind(target).press_key("w", duration_seconds=1.5)

        adapter.execute.assert_called_once_with(
            "press_key", {"key": "w", "duration_seconds": 1.5}
        )

    def test_records_local_step_into_report_log(self, make_bot):
        # press_key bypasses the decision engine, so it must self-record into
        # the local report or it stays invisible there.
        bot = make_bot(report=True)
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        adapter.screenshot.return_value = b""  # bytes → recorded, no disk frame
        bot._adapters[id(target)] = adapter

        before = time.time()
        bot.press_key(target, "Enter")

        assert len(bot._timeline.entries) == 1
        entry = bot._timeline.entries[0]
        assert entry["action_type"] == "press_key"
        assert entry["params"] == {"key": "Enter"}
        # Every entry is timestamped so the report can render a time column
        # and seek the recording to the step.
        assert before <= entry["ts"] <= time.time()

    def test_local_step_skipped_when_reporting_off(self, make_bot):
        bot = make_bot(report=False)
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        bot._adapters[id(target)] = adapter

        bot.press_key(target, "Enter")

        assert bot._timeline.entries == []
        adapter.screenshot.assert_not_called()  # zero overhead when off


class TestClickModifier:
    """click's optional modifier must ride the wire params only when set, so a
    plain click keeps the exact legacy shape report consumers expect."""

    def _bot_with_mock_ai(self, make_bot):
        bot = make_bot()
        bot._ai_action = MagicMock(return_value={"success": True})
        target = object()
        adapter = MagicMock()
        adapter.current_target = target
        bot._adapters[id(target)] = adapter
        return bot, target

    def test_modifier_included_only_when_set(self, make_bot):
        bot, target = self._bot_with_mock_ai(make_bot)

        bot.click(target, "enemy unit", modifier="alt")
        action = bot._ai_action.call_args.kwargs["action"]
        assert action == {
            "type": "click",
            "params": {"locate": "enemy unit", "modifier": "alt"},
        }

        bot._ai_action.reset_mock()
        bot.click(target, "Login button")
        action = bot._ai_action.call_args.kwargs["action"]
        assert action == {"type": "click", "params": {"locate": "Login button"}}

    def test_bound_click_passes_modifier(self, make_bot):
        bot, target = self._bot_with_mock_ai(make_bot)

        bot.bind(target).click("file row", modifier="ctrl+shift")

        action = bot._ai_action.call_args.kwargs["action"]
        assert action["params"] == {"locate": "file row", "modifier": "ctrl+shift"}


class TestAdapterCacheSync:
    """A tab switch makes current_target a *new* page object. The cache must
    re-register it against the same adapter so passing it back doesn't spawn a
    second, divergent adapter (which previously held a closed tab and crashed)."""

    def test_result_registers_returned_target(self, make_bot):
        bot = make_bot()
        p0, v1 = object(), object()  # original page, new-tab page
        adapter = MagicMock()
        adapter.current_target = v1
        bot._adapters[id(p0)] = adapter

        out = bot._result(adapter)

        assert out is v1
        # Passing the returned object back reuses the same adapter (no detect()).
        assert bot._get_adapter(v1) is adapter

    def test_loop_reuses_single_adapter_across_tab_switches(self, make_bot):
        bot = make_bot()
        bot._ai_action = MagicMock(return_value={"success": True})
        p0, v1 = object(), object()
        adapter = MagicMock()
        bot._adapters[id(p0)] = adapter

        adapter.current_target = v1          # click opens a new tab
        out = bot.click(p0, "open")
        assert out is v1

        adapter.current_target = p0          # go_back closes it, back to list
        out2 = bot.go_back(v1)
        assert out2 is p0

        # Both page objects resolve to the one adapter — never a second instance.
        assert bot._get_adapter(p0) is adapter
        assert bot._get_adapter(v1) is adapter


class TestAdapterCacheEviction:
    """The id()-keyed adapter cache must drop entries when the target dies, so
    a long session doesn't grow it unbounded and a recycled id() can't return a
    stale adapter for an unrelated object."""

    def test_entry_evicted_when_target_garbage_collected(self, make_bot):
        import gc

        bot = make_bot()

        class Target:  # plain, weak-referenceable stand-in for a page/driver
            pass

        target = Target()
        bot._cache_adapter(target, MagicMock())
        key = id(target)
        assert key in bot._adapters

        del target
        gc.collect()

        assert key not in bot._adapters


class TestScreenshotConfig:
    def test_default_is_jpeg(self):
        cfg = ScreenshotConfig()
        assert cfg.format == "jpeg"
        assert cfg.extension == "jpg"
        assert cfg.mime_type == "image/jpeg"

    def test_jpg_normalized_to_jpeg(self):
        cfg = ScreenshotConfig(format="jpg")
        assert cfg.format == "jpeg"
        assert cfg.extension == "jpg"
        assert cfg.mime_type == "image/jpeg"

    def test_png_uppercase_normalized(self):
        cfg = ScreenshotConfig(format="PNG")
        assert cfg.format == "png"
        assert cfg.extension == "png"
        assert cfg.mime_type == "image/png"

    @pytest.mark.parametrize("fmt", ["webp", "gif", "bmp", ""])
    def test_unsupported_format_raises(self, fmt):
        with pytest.raises(ValueError, match="unsupported screenshot_format"):
            ScreenshotConfig(format=fmt)


class TestRenderStepImages:
    """The annotated debug image must be encoded in the configured format so its
    bytes match the filename extension _save_frame derives from the config."""

    def _png_bytes(self, w: int = 200, h: int = 150) -> bytes:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
        return buf.getvalue()

    def test_jpeg_config_produces_jpeg(self):
        import io

        from PIL import Image

        full, thumb = _render_step_images(
            self._png_bytes(), (100, 75), ScreenshotConfig(format="jpeg")
        )
        assert Image.open(io.BytesIO(full)).format == "JPEG"
        assert thumb.startswith("data:image/jpeg;base64,")

    def test_png_config_produces_png(self):
        import io

        from PIL import Image

        full, thumb = _render_step_images(
            self._png_bytes(), (100, 75), ScreenshotConfig(format="png")
        )
        assert Image.open(io.BytesIO(full)).format == "PNG"
        assert thumb.startswith("data:image/jpeg;base64,")

    def test_default_config_produces_jpeg(self):
        import io

        from PIL import Image

        # Default config is jpeg; the annotated bytes must not silently be PNG.
        full, _ = _render_step_images(self._png_bytes(), (100, 75))
        assert Image.open(io.BytesIO(full)).format == "JPEG"

    def test_no_coords_still_produces_thumbnail(self):
        # When no coords are supplied, the full image is just re-encoded and we
        # still want a thumbnail for the report.
        full, thumb = _render_step_images(self._png_bytes(), None)
        assert full
        assert thumb.startswith("data:image/jpeg;base64,")


class TestReusedFrame:
    """A step decided on an earlier step's frame (nothing moved the device
    since) still shows that frame in the report — a blank screenshot cell
    reads as a lost capture, not as "the screen didn't change"."""

    def _png_bytes(self, w: int = 200, h: int = 150) -> bytes:
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (w, h), (10, 20, 30)).save(buf, format="PNG")
        return buf.getvalue()

    def _timeline(self, tmp_path):
        from qirabot._timeline import RunTimeline

        return RunTimeline(True, tmp_path, ScreenshotConfig())

    def _shots(self, tmp_path):
        return sorted(p.name for p in (tmp_path / "screenshots").iterdir())

    def test_reuse_points_at_previous_frame_without_writing_a_copy(self, tmp_path):
        tl = self._timeline(tmp_path)
        data = self._png_bytes()
        first = tl.record_step(data, "save_note", {"content": "x"})
        second = tl.record_step(data, "scroll", {"direction": "down"}, reused_frame=True)

        assert second["screenshot"] == first["screenshot"]
        assert second["thumb"] == first["thumb"]
        assert second["reused_frame"] is True
        # Same picture, so no byte-identical second file on disk.
        assert self._shots(tmp_path) == ["001_save_note.jpg"]

    def test_fresh_step_is_not_flagged(self, tmp_path):
        tl = self._timeline(tmp_path)
        entry = tl.record_step(self._png_bytes(), "click", {"locate": "OK"}, (10, 20))
        assert "reused_frame" not in entry

    def test_reuse_with_coords_gets_its_own_annotated_copy(self, tmp_path):
        # The picture repeats but the marker is this step's — drawing it on a
        # shared file would put the wrong coordinates on the earlier step.
        tl = self._timeline(tmp_path)
        data = self._png_bytes()
        first = tl.record_step(data, "save_note", {"content": "x"})
        second = tl.record_step(
            data, "click", {"locate": "OK"}, (10, 20), reused_frame=True
        )

        assert second["screenshot"] != first["screenshot"]
        assert second["reused_frame"] is True
        assert self._shots(tmp_path) == ["001_save_note.jpg", "002_click.jpg"]

    def test_ai_loop_flags_the_step_after_save_note(self, make_bot, tmp_path):
        # save_note doesn't move the device, so the next step reuses its frame.
        png = self._png_bytes()

        class _Adapter(_SettleFakeAdapter):
            def screenshot(self, config=None):
                return png

        bot = make_bot(report=True)
        bot._get_adapter = lambda target: _Adapter()
        bot._execute_action = lambda *a, **k: None
        note = {"success": True, "actionType": "save_note",
                "params": {"content": "found it"}, "finished": False}
        bot._backend.results.extend([
            dict(note),
            {"success": True, "actionType": "scroll",
             "params": {"direction": "down", "amount": 500}, "finished": False},
            dict(note),
        ])
        # ... then the fixture's default terminal done step.
        bot.ai(object(), "do thing", max_steps=5)

        note1, scroll, note2, done = bot._timeline.entries
        # A note is decided on a freshly captured frame; the step after it —
        # here the scroll, and the run-ending done — is not.
        assert "reused_frame" not in note1
        assert "reused_frame" not in note2
        assert scroll["reused_frame"] is True and scroll["thumb"] == note1["thumb"]
        assert done["reused_frame"] is True and done["thumb"] == note2["thumb"]
        # Every step has an image; only the freshly captured ones wrote a file.
        assert all(e["screenshot"] for e in bot._timeline.entries)
        assert len(list((Path(bot.report_dir) / "screenshots").iterdir())) == 2


class TestOpenHeadlessFallback:
    """open(headless=False) on display-less Linux must fall back to headless —
    a headed launch there can only fail (Missing X server or $DISPLAY)."""

    def _launch_kwargs(self, monkeypatch, make_bot, *, platform, display=None, wayland=None):
        """Run bot.open() against a stubbed playwright; return chromium.launch kwargs."""
        import sys as sys_mod

        import qirabot._browser as browser_mod

        fake_pw = MagicMock(name="playwright.sync_api")
        monkeypatch.setattr(browser_mod, "require", lambda module, extra: fake_pw)
        monkeypatch.setattr(sys_mod, "platform", platform)
        for var, value in (("DISPLAY", display), ("WAYLAND_DISPLAY", wayland)):
            if value is None:
                monkeypatch.delenv(var, raising=False)
            else:
                monkeypatch.setenv(var, value)

        bot = make_bot()
        bot.open()
        return fake_pw.sync_playwright().start().chromium.launch.call_args.kwargs

    def test_linux_without_display_falls_back_to_headless(self, monkeypatch, make_bot, caplog):
        with caplog.at_level("WARNING", logger="qirabot"):
            kwargs = self._launch_kwargs(monkeypatch, make_bot, platform="linux")

        assert kwargs["headless"] is True
        assert "no display detected" in caplog.text

    def test_linux_with_display_stays_headed(self, monkeypatch, make_bot):
        kwargs = self._launch_kwargs(
            monkeypatch, make_bot, platform="linux", display=":0"
        )
        assert kwargs["headless"] is False

    def test_linux_with_wayland_stays_headed(self, monkeypatch, make_bot):
        kwargs = self._launch_kwargs(
            monkeypatch, make_bot, platform="linux", wayland="wayland-0"
        )
        assert kwargs["headless"] is False

    def test_non_linux_without_display_stays_headed(self, monkeypatch, make_bot):
        # macOS/Windows always have a display server; env vars are irrelevant.
        kwargs = self._launch_kwargs(monkeypatch, make_bot, platform="darwin")
        assert kwargs["headless"] is False


class TestOpenUserDataDir:
    """open() must expand a leading ~ in user_data_dir — playwright resolves
    the raw string against cwd and would create a literal "~" directory."""

    def test_tilde_is_expanded(self, monkeypatch, make_bot):
        import os

        import qirabot._browser as browser_mod

        fake_pw = MagicMock(name="playwright.sync_api")
        monkeypatch.setattr(browser_mod, "require", lambda module, extra: fake_pw)

        bot = make_bot()
        bot.open(headless=True, user_data_dir="~/.automation")

        call = fake_pw.sync_playwright().start().chromium.launch_persistent_context.call_args
        passed = call.args[0]
        assert passed == os.path.expanduser("~/.automation")
        assert not passed.startswith("~")
