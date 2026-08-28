"""Model-string parsing, project/location resolution and the retry policy."""

import httpx
import pytest

from qirabot.engine.providers.base import ErrorCategory, ProviderError, classify_http_status
from qirabot.engine.providers.registry import (
    DEFAULT_MODELS,
    ModelSpec,
    parse_model,
    resolve_default_model,
    resolve_gemini_api_key,
    resolve_vertex_api_key,
    resolve_vertex_location,
    resolve_vertex_project,
)
from qirabot.engine.providers.retry import (
    JITTER_FRACTION,
    RATE_LIMIT_DELAYS,
    is_retryable,
    jitter,
    with_retry,
)


def _no_jitter(delay: float) -> float:
    return delay


class FakeTokens:
    def token(self) -> str:
        return "tok"

    def adc_project(self) -> str:
        return "adc-proj"


class NoProjectTokens:
    def token(self) -> str:
        return "tok"

    def adc_project(self) -> str:
        return ""


class TestParseModel:
    def test_full_form(self) -> None:
        spec = parse_model("gemini-vertex/gemini-2.5-flash")
        assert spec == ModelSpec(provider="gemini-vertex", model="gemini-2.5-flash")

    def test_model_keeps_inner_slashes(self) -> None:
        spec = parse_model("gemini-vertex/some/slashed-id")
        assert spec == ModelSpec(provider="gemini-vertex", model="some/slashed-id")

    def test_bare_provider_uses_default_model(self) -> None:
        spec = parse_model("gemini-vertex")
        assert spec.model == DEFAULT_MODELS["gemini-vertex"]
        spec = parse_model("gemini")
        assert spec.model == DEFAULT_MODELS["gemini"]

    @pytest.mark.parametrize("bad", ["", "  ", "anthropic/claude-sonnet-4-5", "claude-vertex/claude-sonnet-5", "gemini-api/flash", "vertex-openai/qwen/qwen3-vl-plus"])
    def test_unknown_provider_lists_options(self, bad: str) -> None:
        with pytest.raises(ValueError) as ei:
            parse_model(bad)
        assert "gemini-vertex, gemini" in str(ei.value)

    def test_default_model_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_MODEL", "gemini/gemini-9-flash")
        assert resolve_default_model() == "gemini/gemini-9-flash"
        monkeypatch.delenv("QIRA_MODEL")
        assert parse_model(resolve_default_model())  # built-in default parses


class TestVertexConfig:
    def test_project_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_VERTEX_PROJECT", "env-qira")
        monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-gcp")
        assert resolve_vertex_project("explicit", FakeTokens()) == "explicit"
        assert resolve_vertex_project("", FakeTokens()) == "env-qira"
        monkeypatch.delenv("QIRA_VERTEX_PROJECT")
        assert resolve_vertex_project("", FakeTokens()) == "env-gcp"
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT")
        assert resolve_vertex_project("", FakeTokens()) == "adc-proj"

    def test_project_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QIRA_VERTEX_PROJECT", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
        with pytest.raises(ValueError, match="vertex_project"):
            resolve_vertex_project("", NoProjectTokens())

    def test_location_priority_and_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QIRA_VERTEX_LOCATION", raising=False)
        monkeypatch.delenv("GOOGLE_CLOUD_LOCATION", raising=False)
        assert resolve_vertex_location("") == "global"
        assert resolve_vertex_location("us-east5") == "us-east5"
        monkeypatch.setenv("QIRA_VERTEX_LOCATION", "asia-east1")
        assert resolve_vertex_location("") == "asia-east1"

    def test_api_key_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_VERTEX_API_KEY", "env-key")
        assert resolve_vertex_api_key("explicit") == "explicit"
        assert resolve_vertex_api_key("") == "env-key"
        monkeypatch.delenv("QIRA_VERTEX_API_KEY")
        assert resolve_vertex_api_key("") == ""
        # GOOGLE_API_KEY commonly holds an AI Studio key that Vertex rejects;
        # it is deliberately not read.
        monkeypatch.setenv("GOOGLE_API_KEY", "studio-key")
        assert resolve_vertex_api_key("") == ""

    def test_gemini_api_key_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_GEMINI_API_KEY", "env-qira")
        monkeypatch.setenv("GEMINI_API_KEY", "env-official")
        assert resolve_gemini_api_key("explicit") == "explicit"
        assert resolve_gemini_api_key("") == "env-qira"
        monkeypatch.delenv("QIRA_GEMINI_API_KEY")
        assert resolve_gemini_api_key("") == "env-official"
        monkeypatch.delenv("GEMINI_API_KEY")
        # GOOGLE_API_KEY stays unread here too (could be either key kind).
        monkeypatch.setenv("GOOGLE_API_KEY", "ambiguous")
        assert resolve_gemini_api_key("") == ""


class TestClassify:
    @pytest.mark.parametrize(
        ("status", "want"),
        [
            (429, ErrorCategory.RATE_LIMITED),
            (408, ErrorCategory.TIMEOUT),
            (504, ErrorCategory.TIMEOUT),
            (400, ErrorCategory.INVALID_REQUEST),
            (404, ErrorCategory.INVALID_REQUEST),
            (422, ErrorCategory.INVALID_REQUEST),
            (401, ErrorCategory.AUTH),
            (403, ErrorCategory.AUTH),
            (500, ErrorCategory.UNAVAILABLE),
            (503, ErrorCategory.UNAVAILABLE),
            (418, ErrorCategory.INTERNAL),
        ],
    )
    def test_http_status_table(self, status: int, want: ErrorCategory) -> None:
        assert classify_http_status(status) == want


class TestRetry:
    """The schedule is asserted with jitter stubbed out to the identity;
    TestJitter covers the randomization itself."""

    def test_retries_retryable_then_succeeds(self) -> None:
        sleeps: list[float] = []
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ProviderError("p", "down", category=ErrorCategory.UNAVAILABLE, status_code=503)
            return "ok"

        assert with_retry(fn, sleep=sleeps.append, jitter=_no_jitter) == "ok"
        assert calls["n"] == 3
        # Linear backoff 1s → 2s (3rd attempt succeeds, no third sleep).
        assert sleeps == [1.0, 2.0]

    def test_exhausts_attempts(self) -> None:
        def fn() -> str:
            raise ProviderError("p", "down", category=ErrorCategory.UNAVAILABLE, status_code=503)

        with pytest.raises(ProviderError):
            with_retry(fn, sleep=lambda _: None)

    def test_rate_limit_waits_out_the_quota_window(self) -> None:
        # 429 is a rolling per-minute quota window, not a blip: the schedule
        # must span a full window instead of the generic ~3s, so a long ai()
        # run pauses through the window rather than dying mid-task.
        sleeps: list[float] = []
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 4:
                raise ProviderError("p", "busy", category=ErrorCategory.RATE_LIMITED, status_code=429)
            return "ok"

        assert with_retry(fn, sleep=sleeps.append, jitter=_no_jitter) == "ok"
        assert calls["n"] == 4
        assert sleeps == [5.0, 10.0, 20.0]

    def test_rate_limit_exhausts_after_full_schedule(self) -> None:
        sleeps: list[float] = []
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise ProviderError("p", "busy", category=ErrorCategory.RATE_LIMITED, status_code=429)

        with pytest.raises(ProviderError):
            with_retry(fn, sleep=sleeps.append, jitter=_no_jitter)
        # One initial try plus one per scheduled delay; cumulative wait
        # crosses a one-minute window boundary.
        assert calls["n"] == 5
        assert sleeps == [5.0, 10.0, 20.0, 30.0]
        assert sum(sleeps) >= 60.0

    def test_deterministic_errors_fail_fast(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise ProviderError("p", "denied", category=ErrorCategory.AUTH, status_code=403)

        with pytest.raises(ProviderError):
            with_retry(fn, sleep=lambda _: None)
        assert calls["n"] == 1

    def test_a_read_timeout_is_not_retried(self) -> None:
        # It reached the model and the answer was too slow; each retry costs
        # another full per-call budget to re-ask the same question.
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise ProviderError("p", "slow", category=ErrorCategory.TIMEOUT, status_code=0)

        with pytest.raises(ProviderError):
            with_retry(fn, sleep=lambda _: None)
        assert calls["n"] == 1

    def test_raw_read_timeout_is_not_retried_either(self) -> None:
        assert is_retryable(httpx.ReadTimeout("slow")) is False
        assert is_retryable(httpx.ConnectTimeout("unreachable")) is True
        assert is_retryable(httpx.PoolTimeout("no slot")) is True

    def test_quota_window_can_be_skipped(self) -> None:
        # Callers that already spent a window elsewhere (an escalated tier)
        # put rate limits back on the generic schedule.
        sleeps: list[float] = []

        def fn() -> str:
            raise ProviderError("p", "busy", category=ErrorCategory.RATE_LIMITED, status_code=429)

        with pytest.raises(ProviderError):
            with_retry(fn, sleep=sleeps.append, jitter=_no_jitter, wait_out_quota=False)
        assert sleeps == [1.0, 2.0]

    def test_transport_errors_retryable(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
            return "ok"

        assert with_retry(fn, sleep=lambda _: None) == "ok"
        assert calls["n"] == 2

    def test_generic_exceptions_not_retried(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise RuntimeError("bug")

        with pytest.raises(RuntimeError):
            with_retry(fn, sleep=lambda _: None)
        assert calls["n"] == 1


class TestJitter:
    """Fixed schedules make concurrent runs collide in lockstep; every delay
    is spread by ±JITTER_FRACTION."""

    def test_stays_within_the_band(self) -> None:
        for base in RATE_LIMIT_DELAYS:
            for _ in range(50):
                assert (
                    base * (1 - JITTER_FRACTION)
                    <= jitter(base)
                    <= base * (1 + JITTER_FRACTION)
                )

    def test_actually_varies(self) -> None:
        assert len({jitter(10.0) for _ in range(20)}) > 1

    def test_applied_by_default(self) -> None:
        sleeps: list[float] = []
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 2:
                raise ProviderError("p", "busy", category=ErrorCategory.RATE_LIMITED, status_code=429)
            return "ok"

        assert with_retry(fn, sleep=sleeps.append) == "ok"
        # First rate-limit delay is 5s; jittered, never exactly 5s-scheduled
        # but always inside the band.
        assert len(sleeps) == 1
        assert 4.0 <= sleeps[0] <= 6.0
