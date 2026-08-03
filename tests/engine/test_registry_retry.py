"""Model-string parsing, project/location resolution and the retry policy."""

import httpx
import pytest

from qirabot.engine.providers.base import ErrorCategory, ProviderError, classify_http_status
from qirabot.engine.providers.registry import (
    DEFAULT_MODELS,
    ModelSpec,
    create_provider,
    parse_model,
    resolve_default_model,
    resolve_gemini_api_key,
    resolve_vertex_api_key,
    resolve_vertex_location,
    resolve_vertex_project,
)
from qirabot.engine.providers.retry import with_retry
from qirabot.engine.providers.gemini_vertex import GeminiVertexProvider


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

    def test_create_provider_dispatch(self) -> None:
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        tokens = FakeTokens()
        provider = create_provider(ModelSpec("gemini-vertex", "m"), "p", "global", tokens, client)  # type: ignore[arg-type]
        assert isinstance(provider, GeminiVertexProvider)

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

    def test_create_provider_gemini_requires_key(self) -> None:
        from qirabot.engine.providers.gemini_api import GeminiApiProvider

        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        provider = create_provider(
            ModelSpec("gemini", "m"), "", "", None, client, api_key="sk"
        )
        assert isinstance(provider, GeminiApiProvider)
        with pytest.raises(ValueError, match="QIRA_GEMINI_API_KEY"):
            create_provider(ModelSpec("gemini", "m"), "", "", None, client)

    def test_create_provider_vertex_api_key(self) -> None:
        client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
        provider = create_provider(
            ModelSpec("gemini-vertex", "m"), "", "", None, client, api_key="vk"
        )
        assert isinstance(provider, GeminiVertexProvider)


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
    def test_retries_retryable_then_succeeds(self) -> None:
        sleeps: list[float] = []
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            if calls["n"] < 3:
                raise ProviderError("p", "busy", category=ErrorCategory.RATE_LIMITED, status_code=429)
            return "ok"

        assert with_retry(fn, sleep=sleeps.append) == "ok"
        assert calls["n"] == 3
        # Linear backoff 1s → 2s (3rd attempt succeeds, no third sleep).
        assert sleeps == [1.0, 2.0]

    def test_exhausts_attempts(self) -> None:
        def fn() -> str:
            raise ProviderError("p", "down", category=ErrorCategory.UNAVAILABLE, status_code=503)

        with pytest.raises(ProviderError):
            with_retry(fn, sleep=lambda _: None)

    def test_deterministic_errors_fail_fast(self) -> None:
        calls = {"n": 0}

        def fn() -> str:
            calls["n"] += 1
            raise ProviderError("p", "denied", category=ErrorCategory.AUTH, status_code=403)

        with pytest.raises(ProviderError):
            with_retry(fn, sleep=lambda _: None)
        assert calls["n"] == 1

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
