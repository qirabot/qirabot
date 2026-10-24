"""Same-provider fallback model: when it switches, what it re-sends, how long
it stays switched, and config resolution."""

import logging

import pytest

from qirabot.engine.local_backend import LocalBackend
from qirabot.engine.providers.base import ChatRequest, ChatResponse, ErrorCategory, ProviderError
from qirabot.engine.providers.fallback import FallbackProvider
from qirabot.engine.providers.registry import ModelSpec, resolve_fallback_model
from qirabot.engine.types import Message, TokenUsage, ToolCall

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PRIMARY = "gemini-3.8-flash"
FALLBACK = "gemini-3.5-flash"
DONE_ARGS = {"reason": "done", "success": True, "result": "ok"}
CLICK_ARGS = {"point_x": 500, "point_y": 500, "reason": "r"}


def _err(category: ErrorCategory, status: int = 0) -> ProviderError:
    return ProviderError("gemini-vertex", "boom", category=category, status_code=status)


class ByModel:
    """A provider whose behaviour is scripted per model name: each model
    pops its own queue; an exhausted queue repeats its last entry."""

    def __init__(self, script: dict[str, list[ChatResponse | Exception]]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.models: list[str] = []

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        self.models.append(request.model)
        queue = self.script[request.model]
        r = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(r, Exception):
            raise r
        return r


def _resp(name: str = "done", args: dict | None = None) -> ChatResponse:
    return ChatResponse(
        tool_calls=[ToolCall(id=name, name=name, args=args or DONE_ARGS)],
        token_usage=TokenUsage(input_tokens=10, output_tokens=5),
        model_used="m",
    )


def _req(model: str = PRIMARY) -> ChatRequest:
    return ChatRequest(model=model, messages=[Message(role="user", content="hi")])


def _script(primary: list, fallback: list) -> ByModel:
    return ByModel({PRIMARY: primary, FALLBACK: fallback})


class TestSwitching:
    @pytest.mark.parametrize(
        "category",
        [ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMITED, ErrorCategory.UNAVAILABLE],
    )
    def test_capacity_failures_switch(self, category: ErrorCategory) -> None:
        inner = _script([_err(category)], [_resp()])
        fb = FallbackProvider(inner, FALLBACK)
        fb.chat(_req(), 30.0)
        assert inner.models == [PRIMARY, FALLBACK]
        assert fb.active and fb.fallback_calls == 1

    @pytest.mark.parametrize(
        "category",
        [
            ErrorCategory.AUTH,
            ErrorCategory.INVALID_REQUEST,
            ErrorCategory.CONTENT_BLOCKED,
            ErrorCategory.INTERNAL,
        ],
    )
    def test_deterministic_failures_do_not_switch(self, category: ErrorCategory) -> None:
        inner = _script([_err(category)], [_resp()])
        fb = FallbackProvider(inner, FALLBACK)
        with pytest.raises(ProviderError):
            fb.chat(_req(), 30.0)
        assert inner.models == [PRIMARY]
        assert not fb.active

    def test_only_the_model_changes(self) -> None:
        seen: list[ChatRequest] = []

        class Spy:
            def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
                seen.append(request)
                if request.model == PRIMARY:
                    raise _err(ErrorCategory.TIMEOUT)
                return _resp()

        FallbackProvider(Spy(), FALLBACK).chat(_req(), 30.0)
        assert seen[1].model == FALLBACK
        assert seen[1].messages == seen[0].messages
        # The caller's request is not mutated.
        assert seen[0].model == PRIMARY

    def test_sticky_until_reset(self) -> None:
        inner = _script([_err(ErrorCategory.TIMEOUT), _resp()], [_resp()])
        fb = FallbackProvider(inner, FALLBACK)
        fb.chat(_req(), 30.0)
        fb.chat(_req(), 30.0)
        # No re-probe of the primary while switched.
        assert inner.models == [PRIMARY, FALLBACK, FALLBACK]
        fb.reset()
        fb.chat(_req(), 30.0)
        assert inner.models[-1] == PRIMARY
        assert fb.fallback_calls == 2

    def test_fallback_failure_propagates(self) -> None:
        inner = _script([_err(ErrorCategory.TIMEOUT)], [_err(ErrorCategory.UNAVAILABLE, 503)])
        fb = FallbackProvider(inner, FALLBACK)
        with pytest.raises(ProviderError) as ei:
            fb.chat(_req(), 30.0)
        assert ei.value.category is ErrorCategory.UNAVAILABLE
        assert inner.models == [PRIMARY, FALLBACK]

    def test_switch_is_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        inner = _script([_err(ErrorCategory.TIMEOUT)], [_resp()])
        with caplog.at_level(logging.WARNING, logger="qirabot.engine"):
            FallbackProvider(inner, FALLBACK, "gemini-vertex").chat(_req(), 30.0)
        assert any("switching to fallback model" in r.getMessage() for r in caplog.records)


class TestBackendIntegration:
    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QIRA_TIMEOUT_RETRIES", raising=False)
        monkeypatch.delenv("QIRA_FALLBACK_MODEL", raising=False)

    def _backend(self, inner: ByModel, fallback: str = FALLBACK) -> LocalBackend:
        return LocalBackend(
            model=f"gemini-vertex/{PRIMARY}", provider=inner, fallback_model=fallback
        )

    def test_mid_run_switch_keeps_the_run_going(self) -> None:
        inner = _script(
            [_resp("click", CLICK_ARGS), _err(ErrorCategory.TIMEOUT)],
            [_resp("click", CLICK_ARGS), _resp()],
        )
        b = self._backend(inner)
        run = b.start_ai("task", platform="chrome")
        run.step(PNG, "", 1280, 720)
        run.step(PNG, "ok", 1280, 720)
        outcome = run.step(PNG, "ok", 1280, 720)
        assert outcome.finished and outcome.step_number == 3
        assert inner.models == [PRIMARY, PRIMARY, FALLBACK, FALLBACK]

    def test_next_run_starts_on_the_primary(self) -> None:
        inner = _script([_err(ErrorCategory.TIMEOUT), _resp()], [_resp()])
        b = self._backend(inner)
        b.start_ai("one", platform="chrome").step(PNG, "", 1280, 720)
        b.start_ai("two", platform="chrome").step(PNG, "", 1280, 720)
        assert inner.models == [PRIMARY, FALLBACK, PRIMARY]

    def test_report_names_the_fallback_only_when_used(self) -> None:
        inner = _script([_resp(), _err(ErrorCategory.TIMEOUT)], [_resp()])
        b = self._backend(inner)
        b.start_ai("one", platform="chrome").step(PNG, "", 1280, 720)
        assert b.model_label == f"gemini-vertex/{PRIMARY}"
        b.start_ai("two", platform="chrome").step(PNG, "", 1280, 720)
        assert b.model_label == (
            f"gemini-vertex/{PRIMARY} — 1 call served by fallback {FALLBACK}"
        )

    def test_without_fallback_a_timeout_still_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_TIMEOUT_RETRIES", "0")
        inner = _script([_err(ErrorCategory.TIMEOUT)], [_resp()])
        b = LocalBackend(model=f"gemini-vertex/{PRIMARY}", provider=inner)
        with pytest.raises(ProviderError):
            b.start_ai("t", platform="chrome").step(PNG, "", 1280, 720)
        assert inner.models == [PRIMARY]

    def test_env_configures_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_FALLBACK_MODEL", FALLBACK)
        inner = _script([_err(ErrorCategory.TIMEOUT)], [_resp()])
        b = LocalBackend(model=f"gemini-vertex/{PRIMARY}", provider=inner)
        b.start_ai("t", platform="chrome").step(PNG, "", 1280, 720)
        assert inner.models == [PRIMARY, FALLBACK]


class TestResolution:
    SPEC = ModelSpec(provider="gemini-vertex", model=PRIMARY)

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("QIRA_FALLBACK_MODEL", raising=False)

    def test_unset_is_empty(self) -> None:
        assert resolve_fallback_model("", self.SPEC) == ""

    def test_bare_model_name(self) -> None:
        assert resolve_fallback_model(FALLBACK, self.SPEC) == FALLBACK

    def test_full_form_on_the_same_provider(self) -> None:
        assert resolve_fallback_model(f"gemini-vertex/{FALLBACK}", self.SPEC) == FALLBACK

    def test_other_provider_rejected(self) -> None:
        with pytest.raises(ValueError, match="same provider"):
            resolve_fallback_model(f"gemini/{FALLBACK}", self.SPEC)

    @pytest.mark.parametrize("value", [PRIMARY, f"gemini-vertex/{PRIMARY}", "gemini-vertex"])
    def test_same_as_primary_rejected(self, value: str) -> None:
        # A bare provider name resolves to its default model — the primary.
        with pytest.raises(ValueError, match="same as the primary"):
            resolve_fallback_model(value, self.SPEC)

    def test_explicit_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_FALLBACK_MODEL", "gemini-env")
        assert resolve_fallback_model(FALLBACK, self.SPEC) == FALLBACK
        assert resolve_fallback_model("", self.SPEC) == "gemini-env"

    def test_backend_rejects_bad_config_at_construction(self) -> None:
        with pytest.raises(ValueError, match="same provider"):
            LocalBackend(
                model=f"gemini-vertex/{PRIMARY}",
                provider=_script([_resp()], [_resp()]),
                fallback_model=f"gemini/{FALLBACK}",
            )
