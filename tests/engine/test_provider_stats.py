"""Provider ledger (providers.stats): where model-call wall-clock went.

Driven through the real transports over httpx.MockTransport, with a fake
clock standing in for the wire module's ``time``. Each scripted response
advances it, and so does every backoff sleep, so booked milliseconds are
exact — and the identity "a call's wall-clock = model_ms + the three waits"
can be asserted to the millisecond.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from qirabot.client import SessionUsage, StepResult
from qirabot.engine.local_backend import LocalBackend
from qirabot.engine.providers import _gemini_wire as wire
from qirabot.engine.providers import retry
from qirabot.engine.providers.base import ChatRequest, ErrorCategory, ProviderError
from qirabot.engine.providers.gemini_api import GeminiApiProvider
from qirabot.engine.providers.gemini_vertex import GeminiVertexProvider
from qirabot.engine.providers.service_tier import GEMINI_SERVED_TIER_HEADER
from qirabot.engine.providers.stats import OUTAGE, RATE_LIMIT, ProviderStats, StatsSnapshot
from qirabot.engine.types import Message

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PRIMARY = "gemini-3.8-flash"
FALLBACK = "gemini-3.5-flash"


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tier, escalation, fallback and timeout re-asks each read an env var a
    developer's shell may carry; every case here spells out its own."""
    for var in (
        "QIRA_SERVICE_TIER",
        "QIRA_TIER_ESCALATION",
        "QIRA_FALLBACK_MODEL",
        "QIRA_TIMEOUT_RETRIES",
    ):
        monkeypatch.delenv(var, raising=False)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Requests and backoff sleeps both advance one fake clock.

    Patched at the wire module's import sites: post_json reads
    ``time.monotonic`` there, and with_retry binds its sleep default at
    definition time. Jitter is the identity so schedules are exact."""
    c = Clock()
    monkeypatch.setattr(wire, "time", c)
    real = retry.with_retry
    monkeypatch.setattr(
        wire,
        "with_retry",
        lambda fn, **kw: real(fn, sleep=c.sleep, jitter=lambda d: d, **kw),
    )
    return c


Respond = Callable[[httpx.Request, Clock], httpx.Response]


def _body(action: str, traffic: str) -> dict[str, Any]:
    if action == "done":
        args: dict[str, Any] = {"reason": "r", "success": True, "result": "ok"}
    else:
        args = {"reason": "r", "duration_ms": 100}
    meta: dict[str, Any] = {"promptTokenCount": 5, "candidatesTokenCount": 2}
    if traffic:
        meta["trafficType"] = traffic
    return {
        "candidates": [
            {
                "content": {"parts": [{"functionCall": {"name": action, "args": args}}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": meta,
    }


def answered(seconds: float, *, action: str = "done", traffic: str = "", served: str = "") -> Respond:
    def respond(request: httpx.Request, clock: Clock) -> httpx.Response:
        clock.now += seconds
        headers = {GEMINI_SERVED_TIER_HEADER: served} if served else {}
        return httpx.Response(200, json=_body(action, traffic), headers=headers)

    return respond


def status(code: int, seconds: float = 0.2) -> Respond:
    def respond(request: httpx.Request, clock: Clock) -> httpx.Response:
        clock.now += seconds
        return httpx.Response(code, text="busy")

    return respond


def raises(exc_type: type[httpx.TransportError], seconds: float) -> Respond:
    def respond(request: httpx.Request, clock: Clock) -> httpx.Response:
        clock.now += seconds
        raise exc_type("scripted", request=request)

    return respond


def client(clock: Clock, *script: Respond) -> tuple[httpx.Client, list[httpx.Request]]:
    """Replays `script` in order; the last entry repeats."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return script[min(len(seen) - 1, len(script) - 1)](request, clock)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


class _Tokens:
    def token(self) -> str:
        return "tok"

    def adc_project(self) -> str:
        return "proj"


def vertex(http: httpx.Client, **kw: Any) -> GeminiVertexProvider:
    return GeminiVertexProvider("proj", "global", _Tokens(), http, **kw)  # type: ignore[arg-type]


def chat(provider: Any) -> Any:
    return provider.chat(ChatRequest(model=PRIMARY, messages=[Message(role="user", content="hi")]), 120.0)


def backend(provider: Any, **kw: Any) -> LocalBackend:
    return LocalBackend(model=f"gemini-vertex/{PRIMARY}", provider=provider, **kw)


def step(run: Any, action_result: str = "") -> Any:
    return run.step(PNG, action_result, 1280, 720)


def _err(category: ErrorCategory, status_code: int = 0) -> ProviderError:
    return ProviderError("gemini-vertex", "boom", category=category, status_code=status_code)


class TestWithRetryOnWait:
    @staticmethod
    def _flaky(*errors: Exception) -> Callable[[], str]:
        pending = list(errors)

        def fn() -> str:
            if pending:
                raise pending.pop(0)
            return "ok"

        return fn

    def test_each_backoff_is_reported_as_slept(self) -> None:
        waits: list[tuple[str, float]] = []
        sleeps: list[float] = []
        fn = self._flaky(
            _err(ErrorCategory.RATE_LIMITED, 429), _err(ErrorCategory.UNAVAILABLE, 503)
        )
        result = retry.with_retry(
            fn,
            sleep=sleeps.append,
            jitter=lambda d: d * 2,
            on_wait=lambda category, seconds: waits.append((category, seconds)),
        )
        assert result == "ok"
        # The jittered value is what gets reported — it is what gets slept.
        assert waits == [
            (RATE_LIMIT, retry.RATE_LIMIT_DELAYS[0] * 2),
            (OUTAGE, retry.OUTAGE_DELAYS[1] * 2),
        ]
        assert sleeps == [seconds for _, seconds in waits]

    def test_a_raw_transport_failure_is_an_outage(self) -> None:
        waits: list[str] = []
        retry.with_retry(
            self._flaky(httpx.ConnectError("refused")),
            sleep=lambda _: None,
            on_wait=lambda category, _: waits.append(category),
        )
        assert waits == [OUTAGE]

    def test_no_backoff_means_no_callback(self) -> None:
        waits: list[str] = []
        retry.with_retry(lambda: "ok", on_wait=lambda category, _: waits.append(category))
        with pytest.raises(ProviderError):
            retry.with_retry(
                self._flaky(_err(ErrorCategory.AUTH, 403)),
                on_wait=lambda category, _: waits.append(category),
            )
        assert waits == []


class TestPostJsonBuckets:
    @pytest.mark.parametrize(
        ("respond", "raised", "expected"),
        [
            (answered(1.5), None, StatsSnapshot(model_ms=1_500)),
            (status(429, 0.3), ErrorCategory.RATE_LIMITED, StatsSnapshot(rate_limit_wait_ms=300)),
            (status(503, 20.0), ErrorCategory.UNAVAILABLE, StatsSnapshot(outage_wait_ms=20_000)),
            (raises(httpx.ConnectError, 5.0), httpx.ConnectError, StatsSnapshot(outage_wait_ms=5_000)),
            (
                raises(httpx.ReadTimeout, 120.0),
                ErrorCategory.TIMEOUT,
                StatsSnapshot(timeout_wait_ms=120_000),
            ),
            (status(504, 60.0), ErrorCategory.TIMEOUT, StatsSnapshot(timeout_wait_ms=60_000)),
            # Deterministic: not a capacity problem, booked nowhere.
            (status(400, 0.1), ErrorCategory.INVALID_REQUEST, StatsSnapshot()),
        ],
        ids=["200", "429", "503", "connect-error", "read-timeout", "504", "400"],
    )
    def test_each_outcome_lands_in_its_bucket(
        self, clock: Clock, respond: Respond, raised: Any, expected: StatsSnapshot
    ) -> None:
        http, _ = client(clock, respond)
        stats = ProviderStats()
        post = wire.post_json(http, "https://example.test", {}, "gemini", 120.0, stats=stats)
        if raised is None:
            post({})
        elif isinstance(raised, ErrorCategory):
            with pytest.raises(ProviderError) as exc:
                post({})
            assert exc.value.category == raised
        else:
            with pytest.raises(raised):
                post({})
        assert stats.snapshot() == expected

    def test_fetching_a_token_is_not_model_time(self, clock: Clock) -> None:
        http, _ = client(clock, answered(1.0))
        stats = ProviderStats()

        def slow_headers() -> dict[str, str]:
            clock.now += 7.0  # e.g. an ADC token refresh
            return {}

        wire.post_json(http, "https://example.test", slow_headers, "gemini", 120.0, stats=stats)({})
        assert stats.snapshot() == StatsSnapshot(model_ms=1_000)


class TestTransportLedger:
    def test_refusals_and_the_windows_waited_out_are_booked_together(self, clock: Clock) -> None:
        http, _ = client(clock, status(429, 0.2), status(429, 0.2), answered(1.5))
        provider = vertex(http)
        chat(provider)
        assert provider.stats.snapshot() == StatsSnapshot(
            # two refusals plus the first two quota windows
            rate_limit_wait_ms=200 + 5_000 + 200 + 10_000,
            model_ms=1_500,
        )

    @pytest.mark.parametrize(
        ("make_provider", "script"),
        [
            (vertex, [status(429, 0.2), status(503, 3.0), answered(2.0)]),
            (
                lambda http: vertex(http, tier_escalation=True),
                [raises(httpx.ReadTimeout, 120.0), answered(3.0, traffic="ON_DEMAND_PRIORITY")],
            ),
            (
                lambda http: GeminiApiProvider(
                    "key", http, service_tier="flex", tier_escalation=True
                ),
                [status(503, 25.0), status(503, 25.0), status(503, 25.0), answered(2.0)],
            ),
        ],
        ids=["429-then-503", "timeout-then-escalate", "flex-slow-503s"],
    )
    def test_a_calls_wall_clock_is_fully_accounted_for(
        self, clock: Clock, make_provider: Callable[[httpx.Client], Any], script: list[Respond]
    ) -> None:
        http, _ = client(clock, *script)
        provider = make_provider(http)
        start = clock.now
        chat(provider)
        s = provider.stats.snapshot()
        wall_ms = round((clock.now - start) * 1000)
        assert wall_ms == s.model_ms + s.rate_limit_wait_ms + s.outage_wait_ms + s.timeout_wait_ms

    def test_flex_503s_at_the_server_deadline_are_outage_time(self, clock: Clock) -> None:
        # The Gemini API's flex queue answers 503 at its own X-Server-Timeout
        # (25s on a 30s probe). Counting backoff sleeps alone would book 3s
        # of the 78s this call lost before escalating.
        http, seen = client(
            clock, status(503, 25.0), status(503, 25.0), status(503, 25.0), answered(2.0)
        )
        provider = GeminiApiProvider("key", http, service_tier="flex", tier_escalation=True)
        chat(provider)
        assert seen[0].headers["X-Server-Timeout"] == "25"
        assert provider.stats.snapshot() == StatsSnapshot(
            outage_wait_ms=3 * 25_000 + 1_000 + 2_000, model_ms=2_000
        )

    def test_an_escalated_timeout_is_booked_and_served_at_priority(self, clock: Clock) -> None:
        http, _ = client(
            clock, raises(httpx.ReadTimeout, 120.0), answered(3.0, traffic="ON_DEMAND_PRIORITY")
        )
        provider = vertex(http, tier_escalation=True)
        response = chat(provider)
        assert response.served_tier == "priority"
        assert provider.stats.snapshot() == StatsSnapshot(timeout_wait_ms=120_000, model_ms=3_000)


class TestServedTier:
    @pytest.mark.parametrize(
        ("traffic", "expected"),
        [
            ("ON_DEMAND_PRIORITY", "priority"),
            ("ON_DEMAND_FLEX", "flex"),
            ("ON_DEMAND", "standard"),
            ("", ""),
        ],
    )
    def test_vertex_reads_traffic_type(self, clock: Clock, traffic: str, expected: str) -> None:
        http, _ = client(clock, answered(1.0, traffic=traffic))
        assert chat(vertex(http, service_tier="flex")).served_tier == expected

    @pytest.mark.parametrize(
        ("header", "expected"),
        [("priority", "priority"), ("FLEX", "flex"), ("turbo", ""), ("", "")],
    )
    def test_gemini_api_reads_the_header(self, clock: Clock, header: str, expected: str) -> None:
        http, _ = client(clock, answered(1.0, served=header))
        provider = GeminiApiProvider("key", http, service_tier="priority")
        assert chat(provider).served_tier == expected

    def test_the_step_carries_the_served_tier(self, clock: Clock) -> None:
        http, _ = client(clock, answered(1.0, traffic="ON_DEMAND_PRIORITY"))
        outcome = step(backend(vertex(http)).start_ai("tap ok", platform="chrome"))
        assert outcome.tier == "priority"
        assert outcome.model_used == PRIMARY


class TestBackendSnapshot:
    def test_a_provider_without_a_ledger_reads_as_zero(self) -> None:
        class Bare:
            def chat(self, request: ChatRequest, timeout: float) -> Any:
                raise AssertionError("not called")

        assert backend(Bare()).stats_snapshot() == StatsSnapshot()

    def test_a_fallback_answer_is_on_the_same_ledger(self, clock: Clock) -> None:
        # The primary times out; the fallback model — same provider, same
        # ledger — answers, and the step names the model that did.
        http, seen = client(clock, raises(httpx.ReadTimeout, 120.0), answered(4.0))
        b = backend(vertex(http), fallback_model=FALLBACK)
        outcome = step(b.start_ai("tap ok", platform="chrome"))
        expected = StatsSnapshot(timeout_wait_ms=120_000, model_ms=4_000)
        assert outcome.stats == expected
        assert b.stats_snapshot() == expected
        assert outcome.model_used == FALLBACK
        assert FALLBACK in str(seen[1].url)


class TestStepWindow:
    def test_a_re_asked_timeout_is_part_of_the_step(self, clock: Clock) -> None:
        # Default config: the first decide times out and the session re-asks.
        # The dead ask is part of the step.
        http, _ = client(clock, raises(httpx.ReadTimeout, 120.0), answered(3.0))
        b = backend(vertex(http))
        outcome = step(b.start_ai("tap ok", platform="chrome"))
        assert outcome.stats == StatsSnapshot(timeout_wait_ms=120_000, model_ms=3_000)

    def test_each_step_counts_only_its_own_requests(self, clock: Clock) -> None:
        http, _ = client(clock, status(429, 0.2), answered(1.0, action="wait"), answered(2.0))
        b = backend(vertex(http))
        run = b.start_ai("tap ok", platform="chrome")
        first = step(run)
        second = step(run, "ok")
        assert first.stats == StatsSnapshot(rate_limit_wait_ms=200 + 5_000, model_ms=1_000)
        assert second.stats == StatsSnapshot(model_ms=2_000)
        assert b.stats_snapshot() == StatsSnapshot(rate_limit_wait_ms=5_200, model_ms=3_000)

    def test_a_failed_step_still_lands_in_the_session_totals(self, clock: Clock) -> None:
        http, _ = client(clock, status(429, 0.1))  # refused on every attempt
        b = backend(vertex(http))
        with pytest.raises(ProviderError):
            step(b.start_ai("tap ok", platform="chrome"))
        # Five refusals and the four quota windows between them: over a minute
        # that no StepResult will ever report.
        assert b.stats_snapshot() == StatsSnapshot(
            rate_limit_wait_ms=5 * 100 + 5_000 + 10_000 + 20_000 + 30_000
        )


class TestSnapshotShape:
    def test_subtracting_snapshots_gives_the_window(self) -> None:
        later = StatsSnapshot(model_ms=5_000, timeout_wait_ms=9_000, outage_wait_ms=1_000)
        earlier = StatsSnapshot(model_ms=2_000, timeout_wait_ms=4_000, outage_wait_ms=1_000)
        assert later - earlier == StatsSnapshot(model_ms=3_000, timeout_wait_ms=5_000)

    def test_short_requests_do_not_round_away(self) -> None:
        stats = ProviderStats()
        for _ in range(1_000):
            stats.record_ok(0.0004)
        assert stats.snapshot().model_ms == 400

    @pytest.mark.parametrize("public", [SessionUsage, StepResult])
    def test_every_snapshot_field_is_public(self, public: type) -> None:
        # The snapshot is spread straight into both public types.
        names = {f.name for f in dataclasses.fields(StatsSnapshot)}
        assert names <= {f.name for f in dataclasses.fields(public)}
