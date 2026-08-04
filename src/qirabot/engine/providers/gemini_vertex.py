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
    ) -> None:
        self._project = project
        self._location = location
        self._tokens = token_source
        self._http = http_client
        self._api_key = api_key
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

    def _auth_headers(self) -> dict[str, str]:
        if self._api_key:
            return {"x-goog-api-key": self._api_key}
        assert self._tokens is not None
        return {"Authorization": f"Bearer {self._tokens.token()}"}

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        # _auth_headers is passed as a callable: ADC bearer tokens refresh
        # per request, unlike a static API key.
        post = post_json(
            self._http, self._url(request.model), self._auth_headers, _PROVIDER, timeout
        )
        data, self._part_media_resolution = run_chat(
            request, post, self._part_media_resolution
        )
        return parse_response(data, request.model)
