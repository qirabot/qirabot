"""Consumption tier selection: wire encoding per transport, the served-tier
self-check, escalation on exhaustion, and config resolution.

The two transports encode the same user-facing value differently — Vertex as
a request header, the Gemini Developer API as a body field — and report what
was actually served differently too, so both paths are covered end to end.
"""

import json
from typing import Any

import httpx
import pytest

from qirabot.engine.providers import _gemini_wire as wire, retry, service_tier as st
from qirabot.engine.providers.base import ChatRequest, ErrorCategory, ProviderError
from qirabot.engine.providers.gemini_api import GeminiApiProvider
from qirabot.engine.providers.gemini_vertex import GeminiVertexProvider
from qirabot.engine.providers.registry import (
    ModelSpec,
    check_tier_location,
    create_provider,
    resolve_service_tier,
    resolve_tier_escalation,
)
from qirabot.engine.types import Message

@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 429/503 paths here walk the full rate-limit schedule (>60s of real
    waiting); only the sequence of requests matters.

    Patched at the wire module's import site rather than on time.sleep:
    with_retry binds its sleep default at definition time.
    """
    real = retry.with_retry
    monkeypatch.setattr(
        "qirabot.engine.providers._gemini_wire.with_retry",
        lambda fn, **kw: real(fn, sleep=lambda _: None, **kw),
    )


OK_BODY: dict[str, Any] = {
    "candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
}


def _ok(traffic_type: str = "") -> dict[str, Any]:
    body = json.loads(json.dumps(OK_BODY))
    if traffic_type:
        body["usageMetadata"]["trafficType"] = traffic_type
    return body


def _request() -> ChatRequest:
    return ChatRequest(model="gemini-3.6-flash", messages=[Message(role="user", content="hi")])


class _Tokens:
    def token(self) -> str:
        return "tok"

    def adc_project(self) -> str:
        return "proj"


def _client(
    responses: list[httpx.Response],
) -> tuple[httpx.Client, list[httpx.Request]]:
    """A transport replaying `responses` in order; the last one repeats."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses[min(len(seen) - 1, len(responses) - 1)]

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _vertex(client: httpx.Client, **kw: Any) -> GeminiVertexProvider:
    return GeminiVertexProvider("proj", "global", _Tokens(), client, **kw)  # type: ignore[arg-type]


# -- wire encoding -----------------------------------------------------


class TestVertexHeaders:
    @pytest.mark.parametrize("tier", ["flex", "priority"])
    def test_tier_sets_the_shared_request_type_header(self, tier: str) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        _vertex(client, service_tier=tier).chat(_request(), 30.0)
        assert seen[0].headers[st.VERTEX_TIER_HEADER] == tier

    def test_provisioned_throughput_is_left_first_in_line(self) -> None:
        # Only the shared-request-type header goes out: adding
        # X-Vertex-AI-LLM-Request-Type: shared would bypass PT capacity the
        # user has already paid for.
        client, seen = _client([httpx.Response(200, json=_ok())])
        _vertex(client, service_tier="priority").chat(_request(), 30.0)
        assert "X-Vertex-AI-LLM-Request-Type" not in seen[0].headers

    def test_standard_sends_no_tier_header(self) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        _vertex(client).chat(_request(), 30.0)
        assert st.VERTEX_TIER_HEADER not in seen[0].headers

    def test_tier_is_not_in_the_body(self) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        _vertex(client, service_tier="flex").chat(_request(), 30.0)
        assert "service_tier" not in json.loads(seen[0].content)

    def test_bearer_token_still_refreshes_per_request(self) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        provider = _vertex(client, service_tier="flex")
        provider.chat(_request(), 30.0)
        provider.chat(_request(), 30.0)
        assert all(r.headers["Authorization"] == "Bearer tok" for r in seen)


class TestGeminiApiBody:
    @pytest.mark.parametrize("tier", ["flex", "priority"])
    def test_tier_is_a_top_level_body_field(self, tier: str) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        GeminiApiProvider("k", client, service_tier=tier).chat(_request(), 30.0)
        body = json.loads(seen[0].content)
        # snake_case, sibling of contents — not inside generationConfig.
        assert body["service_tier"] == tier
        assert "service_tier" not in body["generationConfig"]

    def test_standard_sends_no_tier_field(self) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        GeminiApiProvider("k", client).chat(_request(), 30.0)
        assert "service_tier" not in json.loads(seen[0].content)

    def test_flex_bounds_the_server_side_queue_wait(self) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        GeminiApiProvider("k", client, service_tier="flex").chat(_request(), 100.0)
        # Client budget 100 * 1.5 = 150s; the server is told to give up first
        # so the failure is a classifiable 503 rather than a dead connection.
        assert seen[0].headers["X-Server-Timeout"] == "145"

    def test_standard_does_not_bound_the_server(self) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        GeminiApiProvider("k", client, service_tier="priority").chat(_request(), 100.0)
        assert "X-Server-Timeout" not in seen[0].headers


class TestFlexTimeout:
    def test_flex_widens_the_client_budget(self) -> None:
        seen: list[float | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.extensions.get("timeout", {}).get("read"))
            return httpx.Response(200, json=_ok())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        _vertex(client, service_tier="flex").chat(_request(), 60.0)
        _vertex(client, service_tier="priority").chat(_request(), 60.0)
        assert seen == [60.0 * st.FLEX_TIMEOUT_SCALE, 60.0]

    def test_connect_keeps_its_own_short_budget(self) -> None:
        # Reaching the endpoint has nothing to do with how long the model
        # thinks; inheriting a widened flex budget would make an unreachable
        # host take minutes to report itself.
        seen: list[dict[str, float | None]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.extensions.get("timeout", {}))
            return httpx.Response(200, json=_ok())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        _vertex(client, service_tier="flex").chat(_request(), 120.0)
        assert seen[0]["read"] == 180.0
        assert seen[0]["connect"] == wire.CONNECT_TIMEOUT


class TestTimeoutClassification:
    """A read timeout reached the model; anything else never did."""

    def test_read_timeout_is_a_timeout_and_is_not_retried(self) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            raise httpx.ReadTimeout("model too slow")

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(ProviderError) as ei:
            _vertex(client).chat(_request(), 30.0)
        assert ei.value.category is ErrorCategory.TIMEOUT
        assert attempts["n"] == 1

    @pytest.mark.parametrize(
        "exc",
        [httpx.ConnectTimeout("unreachable"), httpx.PoolTimeout("no slot")],
    )
    def test_unreached_endpoint_is_unavailable_and_is_retried(self, exc: Exception) -> None:
        attempts = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            attempts["n"] += 1
            raise exc

        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(ProviderError) as ei:
            _vertex(client).chat(_request(), 30.0)
        assert ei.value.category is ErrorCategory.UNAVAILABLE
        assert attempts["n"] == 3


# -- served-tier self-check --------------------------------------------


class TestServedTierCheck:
    def test_vertex_traffic_type_reaches_the_response(self) -> None:
        client, _ = _client([httpx.Response(200, json=_ok("ON_DEMAND_PRIORITY"))])
        resp = _vertex(client, service_tier="priority").chat(_request(), 30.0)
        assert resp.traffic_type == "ON_DEMAND_PRIORITY"

    def test_vertex_downgrade_warns_once(self, caplog: pytest.LogCaptureFixture) -> None:
        # Over capacity, Priority is served as Standard with a 200 and no
        # error; the only signal is trafficType.
        client, _ = _client([httpx.Response(200, json=_ok("ON_DEMAND"))])
        provider = _vertex(client, service_tier="priority")
        with caplog.at_level("WARNING", logger="qirabot.engine"):
            provider.chat(_request(), 30.0)
            provider.chat(_request(), 30.0)
        warnings = [r for r in caplog.records if "served as standard" in r.getMessage()]
        assert len(warnings) == 1

    def test_vertex_honored_tier_is_silent(self, caplog: pytest.LogCaptureFixture) -> None:
        client, _ = _client([httpx.Response(200, json=_ok("ON_DEMAND_FLEX"))])
        with caplog.at_level("WARNING", logger="qirabot.engine"):
            _vertex(client, service_tier="flex").chat(_request(), 30.0)
        assert not caplog.records

    def test_standard_never_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        client, _ = _client([httpx.Response(200, json=_ok("ON_DEMAND"))])
        with caplog.at_level("WARNING", logger="qirabot.engine"):
            _vertex(client).chat(_request(), 30.0)
        assert not caplog.records

    def test_the_warning_names_the_config_it_saw(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # The endpoint reports no reason, so the warning has to hand the
        # reader enough to rule out the documented preconditions themselves.
        client, _ = _client([httpx.Response(200, json=_ok("ON_DEMAND"))])
        with caplog.at_level("WARNING", logger="qirabot.engine"):
            _vertex(client, service_tier="priority").chat(_request(), 30.0)
        msg = caplog.records[0].getMessage()
        assert "model=gemini-3.6-flash" in msg
        assert "location=global" in msg
        assert "no reason" in msg
        assert "entitlement" in msg

    def test_gemini_api_reads_the_response_header(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client, _ = _client([
            httpx.Response(200, json=_ok(), headers={st.GEMINI_SERVED_TIER_HEADER: "standard"})
        ])
        with caplog.at_level("WARNING", logger="qirabot.engine"):
            GeminiApiProvider("k", client, service_tier="priority").chat(_request(), 30.0)
        assert any("served as standard" in r.getMessage() for r in caplog.records)

    def test_missing_signal_is_not_a_downgrade(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # An endpoint that reports nothing must not be read as a downgrade.
        client, _ = _client([httpx.Response(200, json=_ok())])
        with caplog.at_level("WARNING", logger="qirabot.engine"):
            GeminiApiProvider("k", client, service_tier="flex").chat(_request(), 30.0)
        assert not caplog.records


# -- escalation --------------------------------------------------------


class TestEscalate:
    @pytest.mark.parametrize(
        ("tier", "want"),
        [("flex", "standard"), ("standard", "priority"), ("", "priority"), ("priority", "")],
    )
    def test_ladder(self, tier: str, want: str) -> None:
        exc = ProviderError("p", "busy", category=ErrorCategory.RATE_LIMITED, status_code=429)
        assert st.escalate(tier, exc) == want

    @pytest.mark.parametrize(
        "category",
        [ErrorCategory.AUTH, ErrorCategory.INVALID_REQUEST, ErrorCategory.CONTENT_BLOCKED],
    )
    def test_deterministic_failures_do_not_escalate(self, category: ErrorCategory) -> None:
        # A different tier would fail these identically.
        assert st.escalate("standard", ProviderError("p", "no", category=category)) == ""

    def test_flex_treats_a_timeout_as_a_capacity_failure(self) -> None:
        # Flex's characteristic failure is a queue that never reaches the
        # request: the budget expires instead of the endpoint refusing. That
        # is the case escalation exists for, so it must not be missed.
        exc = ProviderError("p", "slow", category=ErrorCategory.TIMEOUT)
        assert st.escalate("flex", exc) == "standard"

    @pytest.mark.parametrize("tier", ["standard", "", "priority"])
    def test_promptly_served_tiers_do_not_escalate_on_timeout(self, tier: str) -> None:
        # There a timeout means a slow generation, which another tier will
        # not fix.
        exc = ProviderError("p", "slow", category=ErrorCategory.TIMEOUT)
        assert st.escalate(tier, exc) == ""

    def test_disabled_by_default(self) -> None:
        client, seen = _client([httpx.Response(429, json={"error": "quota"})])
        with pytest.raises(ProviderError):
            _vertex(client).chat(_request(), 30.0)
        # 1 initial + 4 rate-limit retries, and no priority attempt.
        assert all(st.VERTEX_TIER_HEADER not in r.headers for r in seen)

    def test_retries_one_rung_up_after_backoff(self) -> None:
        client, seen = _client([
            httpx.Response(429, json={"error": "quota"}),
            httpx.Response(429, json={"error": "quota"}),
            httpx.Response(429, json={"error": "quota"}),
            httpx.Response(429, json={"error": "quota"}),
            httpx.Response(429, json={"error": "quota"}),
            httpx.Response(200, json=_ok("ON_DEMAND_PRIORITY")),
        ])
        resp = _vertex(client, tier_escalation=True).chat(_request(), 30.0)
        assert resp.traffic_type == "ON_DEMAND_PRIORITY"
        # The free remedy runs to exhaustion first: escalation is the last
        # move before losing the run, not the first response to a 429.
        assert [st.VERTEX_TIER_HEADER in r.headers for r in seen] == [
            False, False, False, False, False, True
        ]
        assert seen[-1].headers[st.VERTEX_TIER_HEADER] == "priority"

    def test_escalates_only_once(self) -> None:
        client, seen = _client([httpx.Response(429, json={"error": "quota"})])
        with pytest.raises(ProviderError):
            _vertex(client, tier_escalation=True).chat(_request(), 30.0)
        # One full quota window on standard (5), then priority on the generic
        # budget (3) — not a second minute of sleeping.
        assert len(seen) == 8

    def test_flex_escalates_on_capacity_shedding(self) -> None:
        # Flex is sheddable and answers 503 when it cannot be placed.
        client, seen = _client([
            httpx.Response(503, json={"error": "at capacity"}),
            httpx.Response(503, json={"error": "at capacity"}),
            httpx.Response(503, json={"error": "at capacity"}),
            httpx.Response(200, json=_ok()),
        ])
        GeminiApiProvider("k", client, service_tier="flex", tier_escalation=True).chat(
            _request(), 30.0
        )
        assert json.loads(seen[0].content)["service_tier"] == "flex"
        # Standard is the absence of a selector, never a literal value.
        assert "service_tier" not in json.loads(seen[-1].content)

    def test_escalating_to_standard_sends_no_vertex_header(self) -> None:
        client, seen = _client([
            httpx.Response(503, json={"error": "at capacity"}),
            httpx.Response(503, json={"error": "at capacity"}),
            httpx.Response(503, json={"error": "at capacity"}),
            httpx.Response(200, json=_ok()),
        ])
        _vertex(client, service_tier="flex", tier_escalation=True).chat(_request(), 30.0)
        assert seen[0].headers[st.VERTEX_TIER_HEADER] == "flex"
        assert st.VERTEX_TIER_HEADER not in seen[-1].headers


class TestStickiness:
    """Escalation parks: a congested tier is discovered once per provider,
    not re-discovered on every step of an ai() run."""

    def _congested_then_ok(self) -> tuple[httpx.Client, list[str]]:
        tiers: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            tier = request.headers.get(st.VERTEX_TIER_HEADER, "standard")
            tiers.append(tier)
            if tier == "flex":
                return httpx.Response(503, json={"error": "at capacity"})
            return httpx.Response(200, json=_ok())

        return httpx.Client(transport=httpx.MockTransport(handler)), tiers

    def test_later_calls_skip_the_congested_tier(self) -> None:
        client, tiers = self._congested_then_ok()
        provider = _vertex(client, service_tier="flex", tier_escalation=True)
        for _ in range(4):
            provider.chat(_request(), 30.0)
        # One probe, then straight to standard for the rest of the session.
        assert tiers == ["flex", "standard", "standard", "standard", "standard"]

    def test_it_warns_once_and_names_the_new_home(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client, _ = self._congested_then_ok()
        provider = _vertex(client, service_tier="flex", tier_escalation=True)
        with caplog.at_level("WARNING", logger="qirabot.engine"):
            provider.chat(_request(), 30.0)
            provider.chat(_request(), 30.0)
        moves = [r for r in caplog.records if "rest of this session" in r.getMessage()]
        assert len(moves) == 1

    def test_without_escalation_it_keeps_trying_the_tier(self) -> None:
        client, tiers = self._congested_then_ok()
        provider = _vertex(client, service_tier="flex")
        for _ in range(2):
            with pytest.raises(ProviderError):
                provider.chat(_request(), 30.0)
        # Nowhere to park, so every call still asks for flex.
        assert set(tiers) == {"flex"}


class TestFlexProbeBudget:
    def _read_timeout(self, provider: Any, budget: float) -> float:
        seen: list[float] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.extensions["timeout"]["read"])
            return httpx.Response(200, json=_ok())

        provider._http = httpx.Client(transport=httpx.MockTransport(handler))
        provider.chat(_request(), budget)
        return seen[0]

    def test_a_probe_gets_a_short_leash(self) -> None:
        # With standard one escalation away, waiting out a queue is pure
        # stall: cap the attempt well below the call's own budget.
        provider = _vertex(_client([])[0], service_tier="flex", tier_escalation=True)
        assert self._read_timeout(provider, 120.0) == st.FLEX_PROBE_TIMEOUT

    def test_a_probe_never_exceeds_the_callers_budget(self) -> None:
        provider = _vertex(_client([])[0], service_tier="flex", tier_escalation=True)
        assert self._read_timeout(provider, 10.0) == 10.0

    def test_without_escalation_flex_keeps_the_widened_budget(self) -> None:
        # Nowhere to hand off to, so waiting is the only option.
        provider = _vertex(_client([])[0], service_tier="flex")
        assert self._read_timeout(provider, 120.0) == 120.0 * st.FLEX_TIMEOUT_SCALE


class TestRetryBudget:
    """How much to spend fighting inside a tier before handing off."""

    def test_flex_with_somewhere_to_go_tries_once(self) -> None:
        assert st.retry_budget("flex", True, False, 3) == st.RetryBudget(1, True)

    @pytest.mark.parametrize(
        ("tier", "escalation"),
        [("flex", False), ("priority", True), ("", True), ("standard", False)],
    )
    def test_everyone_else_keeps_the_normal_budget(self, tier: str, escalation: bool) -> None:
        assert st.retry_budget(tier, escalation, False, 3) == st.RetryBudget(3, True)

    def test_an_escalated_call_skips_the_quota_window(self) -> None:
        # The tier below already waited one out, and the tiers commonly share
        # the bucket, so a second minute stalls the run for nothing.
        assert st.retry_budget("priority", True, True, 3) == st.RetryBudget(3, False)

    def test_flex_hands_off_after_a_single_shed(self) -> None:
        # A queue deep enough to shed will not have drained a second later,
        # and each flex retry costs a full widened timeout.
        client, seen = _client([
            httpx.Response(503, json={"error": "at capacity"}),
            httpx.Response(200, json=_ok()),
        ])
        _vertex(client, service_tier="flex", tier_escalation=True).chat(_request(), 30.0)
        assert len(seen) == 2
        assert seen[0].headers[st.VERTEX_TIER_HEADER] == "flex"

    def test_flex_without_escalation_keeps_retrying(self) -> None:
        # Nowhere to hand off to, so the retries are all the caller has.
        client, seen = _client([httpx.Response(503, json={"error": "at capacity"})])
        with pytest.raises(ProviderError):
            _vertex(client, service_tier="flex").chat(_request(), 30.0)
        assert len(seen) == 3

    def test_a_timed_out_flex_call_escalates_instead_of_retrying(self) -> None:
        attempts: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            tier = request.headers.get(st.VERTEX_TIER_HEADER, "standard")
            attempts.append(tier)
            if tier == "flex":
                raise httpx.ReadTimeout("queue never reached us")
            return httpx.Response(200, json=_ok())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        _vertex(client, service_tier="flex", tier_escalation=True).chat(_request(), 30.0)
        # One flex attempt, not three: each would burn the whole budget.
        assert attempts == ["flex", "standard"]


# -- config resolution -------------------------------------------------


class TestResolution:
    def test_normalize_treats_standard_as_the_default(self) -> None:
        assert st.normalize("") == ""
        assert st.normalize("standard") == ""
        assert st.normalize(" Flex ") == "flex"

    def test_unknown_tier_fails_fast(self) -> None:
        with pytest.raises(ValueError, match="unknown service_tier"):
            st.normalize("cheap")

    def test_explicit_beats_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_SERVICE_TIER", "flex")
        assert resolve_service_tier("priority") == "priority"
        assert resolve_service_tier("") == "flex"
        monkeypatch.delenv("QIRA_SERVICE_TIER")
        assert resolve_service_tier("") == ""

    def test_bad_env_value_fails_fast(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_SERVICE_TIER", "turbo")
        with pytest.raises(ValueError, match="unknown service_tier"):
            resolve_service_tier("")

    def test_escalation_is_tri_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("QIRA_TIER_ESCALATION", "1")
        # An explicit False must beat an env var left on elsewhere.
        assert resolve_tier_escalation(False) is False
        assert resolve_tier_escalation(None) is True
        monkeypatch.setenv("QIRA_TIER_ESCALATION", "no")
        assert resolve_tier_escalation(None) is False
        monkeypatch.delenv("QIRA_TIER_ESCALATION")
        assert resolve_tier_escalation(None) is False

    def test_regional_endpoint_is_rejected(self) -> None:
        # The header is accepted and ignored off the global endpoint, so the
        # whole run would bill at standard rates while looking configured.
        with pytest.raises(ValueError, match="global Vertex endpoint"):
            check_tier_location("priority", "us-central1")
        check_tier_location("", "us-central1")
        check_tier_location("priority", "global")

    def test_create_provider_threads_the_tier(self) -> None:
        client, seen = _client([httpx.Response(200, json=_ok())])
        provider = create_provider(
            ModelSpec("gemini", "m"), "", "", None, client, api_key="k", tier="flex"
        )
        provider.chat(_request(), 30.0)
        assert json.loads(seen[0].content)["service_tier"] == "flex"
