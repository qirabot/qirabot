"""Gemini via the Gemini Developer API (AI Studio API keys).

Same generateContent wire format as gemini_vertex — request building and
response parsing are imported from there. Only the transport differs:
generativelanguage.googleapis.com with a models/{model} path (no
project/location, no publishers/ prefix) and x-goog-api-key auth.
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import ChatRequest, ChatResponse, ErrorCategory, ProviderError, http_error
from .gemini_vertex import build_request_body, parse_response
from .retry import with_retry

_PROVIDER = "gemini"

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiApiProvider:
    def __init__(self, api_key: str, http_client: httpx.Client) -> None:
        self._api_key = api_key
        self._http = http_client

    def _url(self, model: str) -> str:
        return f"{_BASE_URL}/models/{model}:generateContent"

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        body = build_request_body(request)
        url = self._url(request.model)

        def call() -> dict[str, Any]:
            try:
                resp = self._http.post(
                    url,
                    json=body,
                    headers={"x-goog-api-key": self._api_key},
                    timeout=timeout,
                )
            except httpx.TimeoutException as exc:
                raise ProviderError(
                    _PROVIDER, f"request timed out: {exc}", category=ErrorCategory.TIMEOUT
                ) from exc
            if resp.status_code != 200:
                raise http_error(_PROVIDER, resp.status_code, resp.text)
            data = resp.json()
            if not isinstance(data, dict):
                raise ProviderError(_PROVIDER, "non-object response body")
            return data

        return parse_response(with_retry(call), request.model)
