"""Gemini via the Gemini Developer API (AI Studio API keys).

Transport only: generativelanguage.googleapis.com with a models/{model} path
(no project/location, no publishers/ prefix) and x-goog-api-key auth. The
generateContent wire format is shared with the Vertex transport and lives in
:mod:`._gemini_wire`.
"""

from __future__ import annotations

import httpx

from ._gemini_wire import parse_response, post_json, run_chat
from .base import ChatRequest, ChatResponse
from .retry import DEFAULT_ATTEMPTS
from .service_tier import (
    FLEX,
    GEMINI_SERVED_TIER_HEADER,
    TierCheck,
    TierLadder,
)

_PROVIDER = "gemini"

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Margin between the server's own deadline hint and the client timeout, so
# the server gives up first and answers with a classifiable 503 instead of
# the client tearing down a connection it can tell nothing from.
_SERVER_TIMEOUT_MARGIN = 5.0


class GeminiApiProvider:
    def __init__(
        self,
        api_key: str,
        http_client: httpx.Client,
        service_tier: str = "",
        tier_escalation: bool = False,
    ) -> None:
        self._api_key = api_key
        self._http = http_client
        self._ladder = TierLadder(service_tier, tier_escalation, _PROVIDER)
        self._tier_check = TierCheck(_PROVIDER)
        # Sticky capability flag, see run_chat.
        self._part_media_resolution = True

    def _url(self, model: str) -> str:
        return f"{_BASE_URL}/models/{model}:generateContent"

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        return self._ladder.run(
            lambda tier, escalated: self._chat_once(request, timeout, tier, escalated)
        )

    def _chat_once(
        self, request: ChatRequest, timeout: float, tier: str, escalated: bool
    ) -> ChatResponse:
        budget = self._ladder.budget(escalated, DEFAULT_ATTEMPTS)
        timeout = self._ladder.timeout_for(tier, timeout)
        headers = {"x-goog-api-key": self._api_key}
        if tier == FLEX:
            # Flex requests queue. Bound the wait server-side rather than
            # letting an interactive step hang for the endpoint's default
            # ten minutes; over capacity we would rather fail and escalate.
            headers["X-Server-Timeout"] = str(max(1, int(timeout - _SERVER_TIMEOUT_MARGIN)))

        # The served tier only comes back as a response header here — unlike
        # Vertex, the body carries no trafficType.
        served: list[str] = []
        post = post_json(
            self._http,
            self._url(request.model),
            headers,
            _PROVIDER,
            timeout,
            on_headers=lambda h: served.append(h.get(GEMINI_SERVED_TIER_HEADER, "")),
        )
        data, self._part_media_resolution = run_chat(
            request,
            post,
            self._part_media_resolution,
            service_tier=tier,
            attempts=budget.attempts,
            wait_out_quota=budget.wait_out_quota,
        )
        resp = parse_response(data, request.model)
        self._tier_check.observe(
            tier,
            served[-1].strip().lower() if served else "",
            request.model,
            "endpoint=generativelanguage",
        )
        return resp
