"""Tests for retry logic in Qirabot client.

v3: single-step actions retry retryable QirabotErrors inside ``_ai_action``
(exponential backoff, max attempts = retry+1); the multi-step ai() loop has
no retry of its own — the engine's provider layer handles transport blips.
"""

from unittest.mock import MagicMock

import pytest

from qirabot.adapters.base import DeviceAdapter, DeviceInfo
from qirabot.exceptions import (
    ActionError,
    AuthenticationError,
    InsufficientBalanceError,
    QirabotError,
    QirabotTimeoutError,
    RateLimitError,
    _is_retryable,
)


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


class TestIsRetryable:
    def test_timeout_is_retryable(self):
        assert _is_retryable(QirabotTimeoutError("timeout")) is True

    def test_server_error_is_retryable(self):
        assert _is_retryable(QirabotError("fail", status_code=500)) is True

    def test_502_is_retryable(self):
        assert _is_retryable(QirabotError("bad gateway", status_code=502)) is True

    def test_429_is_retryable(self):
        assert _is_retryable(QirabotError("rate limit", status_code=429)) is True

    def test_rate_limit_error_is_retryable(self):
        assert _is_retryable(RateLimitError("slow down", status_code=429)) is True

    def test_408_is_retryable(self):
        assert _is_retryable(QirabotError("request timeout", status_code=408)) is True

    def test_auth_error_not_retryable(self):
        assert _is_retryable(AuthenticationError("bad key", status_code=401)) is False

    def test_balance_error_not_retryable(self):
        assert _is_retryable(InsufficientBalanceError("no credits", status_code=402)) is False

    def test_400_not_retryable(self):
        assert _is_retryable(QirabotError("bad request", status_code=400)) is False

    def test_no_status_code_is_retryable(self):
        assert _is_retryable(QirabotError("generic error")) is True


class TestRetry:
    def _bot(self, make_bot, retry=2, retry_delay=0.01, **kw):
        bot = make_bot(retry=retry, retry_delay=retry_delay, **kw)
        bot._get_adapter = lambda target: _FakeAdapter()
        return bot

    def test_retry_on_transient_error(self, make_bot):
        # Two retryable failures (an exception, then a success=False response
        # body — which raises a retryable ActionError), then the default
        # success response: the click must go through on the third attempt.
        bot = self._bot(make_bot)
        bot._backend.results.extend([
            QirabotError("server error", status_code=500),
            {"success": False, "error": "transient decision failure"},
        ])
        bot.click("target", "btn")
        assert len(bot._backend.requests) == 3

    def test_error_response_exhausts_retries(self, make_bot):
        bot = self._bot(make_bot, retry=1)
        bot._backend.results.extend([
            {"success": False, "error": "boom"},
            {"success": False, "error": "boom"},
        ])
        with pytest.raises(ActionError, match="boom"):
            bot.click("target", "btn")
        assert len(bot._backend.requests) == 2  # 1 + 1 retry

    def test_no_retry_on_auth_error(self, make_bot):
        bot = self._bot(make_bot)
        bot._backend.results.append(AuthenticationError("bad key", status_code=401))
        with pytest.raises(AuthenticationError):
            bot.click("target", "btn")
        assert len(bot._backend.requests) == 1

    def test_no_retry_on_balance_error(self, make_bot):
        bot = self._bot(make_bot)
        bot._backend.results.append(InsufficientBalanceError("no credits", status_code=402))
        with pytest.raises(InsufficientBalanceError):
            bot.click("target", "btn")
        assert len(bot._backend.requests) == 1

    def test_raises_after_max_retries(self, make_bot):
        bot = self._bot(make_bot, retry=2)
        bot._backend.results.extend([QirabotTimeoutError("timeout")] * 3)
        with pytest.raises(QirabotTimeoutError):
            bot.click("target", "btn")
        assert len(bot._backend.requests) == 3  # 1 + 2 retries

    def test_exponential_backoff_delays(self, make_bot, monkeypatch):
        sleeps = []
        monkeypatch.setattr("qirabot.client.time.sleep", lambda s: sleeps.append(s))
        bot = self._bot(make_bot, retry=2, retry_delay=0.5)
        bot._backend.results.extend([QirabotTimeoutError("t1"), QirabotTimeoutError("t2")])
        bot.click("target", "btn")
        assert sleeps == [0.5, 1.0]  # retry_delay * 2**attempt

    def test_per_call_retry_override(self, make_bot):
        bot = self._bot(make_bot, retry=0)  # instance default: no retry
        bot._backend.results.extend([
            QirabotError("fail", status_code=500),
            QirabotError("fail", status_code=500),
        ])
        with pytest.raises(QirabotError):
            bot.click("target", "btn", retry=1)
        assert len(bot._backend.requests) == 2  # 1 + 1 retry

    def test_retry_zero_means_no_retry(self, make_bot):
        bot = self._bot(make_bot, retry=0)
        bot._backend.results.append(QirabotError("fail", status_code=500))
        with pytest.raises(QirabotError):
            bot.click("target", "btn")
        assert len(bot._backend.requests) == 1

    def test_click_passes_retry(self, make_bot):
        bot = self._bot(make_bot)
        bot._ai_action = MagicMock(return_value={"success": True})
        bot.click("target", "button", retry=3)
        assert bot._ai_action.call_args.kwargs["retry"] == 3

    def test_init_stores_retry_params(self, make_bot):
        bot = make_bot(retry=5, retry_delay=2.0)
        assert bot._retry == 5
        assert bot._retry_delay == 2.0


class TestAiLoopNoRetry:
    """v3: the ai() loop makes exactly one backend call per step — a transient
    error is not retried at the loop level (the engine's provider layer owns
    transport retries; SDK-level retry is a single-step-action feature)."""

    def _bot(self, make_bot):
        bot = make_bot(retry=3, retry_delay=0.01)
        bot._get_adapter = lambda target: _FakeAdapter()
        return bot

    def test_backend_exception_propagates_without_retry(self, make_bot):
        bot = self._bot(make_bot)
        bot._backend.results.append(QirabotTimeoutError("transient"))
        with pytest.raises(QirabotTimeoutError):
            bot.ai(object(), "do it", max_steps=5)
        assert len(bot._backend.requests) == 1  # retry=3 did not apply

    def test_step_error_raises_action_error_out_of_loop(self, make_bot):
        # A success=False & finished=False step body ends the run by raising,
        # without any loop-level retry.
        bot = self._bot(make_bot)
        bot._backend.results.append(
            {"success": False, "finished": False, "error": "decision failed"}
        )
        with pytest.raises(ActionError, match="decision failed"):
            bot.ai(object(), "do it", max_steps=5)
        assert len(bot._backend.requests) == 1
