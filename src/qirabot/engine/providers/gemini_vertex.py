"""Gemini on Vertex AI via the generateContent REST API.

Transport only: URL construction (regional ADC endpoint, or the global
endpoint for Vertex API keys) and auth headers. The wire format — request
building, the per-part mediaResolution capability fallback, response
parsing — is shared with the Gemini Developer API transport and lives in
:mod:`._gemini_wire`.
"""

from __future__ import annotations

import httpx

from ._gemini_wire import parse_response, post_json, run_chat
from .base import ChatRequest, ChatResponse
from .service_tier import (
    FLEX,
    FLEX_TIMEOUT_SCALE,
    VERTEX_TIER_HEADER,
    TierCheck,
    tier_from_traffic_type,
    with_escalation,
)
from .vertex_auth import VertexTokenSource, vertex_base_url

# API-key requests bypass project/location entirely: Vertex API keys are
# bound to a project server-side and only the global endpoint accepts them,
# so the path is the short publishers/... form (same wire format as
# google-genai's Client(vertexai=True, api_key=...)).
_API_KEY_BASE_URL = "https://aiplatform.googleapis.com/v1"

_PROVIDER = "gemini-vertex"


class GeminiVertexProvider:
    def __init__(
        self,
        project: str,
        location: str,
        token_source: VertexTokenSource | None,
        http_client: httpx.Client,
        api_key: str = "",
        service_tier: str = "",
        tier_escalation: bool = False,
    ) -> None:
        self._project = project
        self._location = location
        self._tokens = token_source
        self._http = http_client
        self._api_key = api_key
        self._service_tier = service_tier
        self._tier_escalation = tier_escalation
        self._tier_check = TierCheck(_PROVIDER)
        # Sticky capability flag: flips off on the first endpoint rejection
        # of per-part mediaResolution, so only one request is ever wasted.
        self._part_media_resolution = True

    def _url(self, model: str) -> str:
        if self._api_key:
            return f"{_API_KEY_BASE_URL}/publishers/google/models/{model}:generateContent"
        return (
            f"{vertex_base_url(self._location)}/projects/{self._project}"
            f"/locations/{self._location}/publishers/google/models/{model}:generateContent"
        )

    def _headers(self, tier: str) -> dict[str, str]:
        if self._api_key:
            headers = {"x-goog-api-key": self._api_key}
        else:
            assert self._tokens is not None
            headers = {"Authorization": f"Bearer {self._tokens.token()}"}
        if tier:
            # Only the shared-request-type header. Leaving
            # X-Vertex-AI-LLM-Request-Type unset keeps any Provisioned
            # Throughput quota first in line — bypassing capacity the user
            # has already paid for would be the wrong default, and for
            # everyone without PT the two forms behave identically.
            headers[VERTEX_TIER_HEADER] = tier
        return headers

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        return with_escalation(
            self._service_tier,
            self._tier_escalation,
            _PROVIDER,
            lambda tier: self._chat_once(request, timeout, tier),
        )

    def _chat_once(self, request: ChatRequest, timeout: float, tier: str) -> ChatResponse:
        if tier == FLEX:
            timeout *= FLEX_TIMEOUT_SCALE
        # The header factory is passed as a callable: ADC bearer tokens
        # refresh per request, unlike a static API key.
        post = post_json(
            self._http,
            self._url(request.model),
            lambda: self._headers(tier),
            _PROVIDER,
            timeout,
        )
        data, self._part_media_resolution = run_chat(
            request, post, self._part_media_resolution
        )
        resp = parse_response(data, request.model)
        self._tier_check.observe(tier, tier_from_traffic_type(resp.traffic_type))
        return resp
