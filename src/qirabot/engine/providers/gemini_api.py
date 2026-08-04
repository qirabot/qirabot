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

_PROVIDER = "gemini"

_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class GeminiApiProvider:
    def __init__(self, api_key: str, http_client: httpx.Client) -> None:
        self._api_key = api_key
        self._http = http_client
        # Sticky capability flag, see run_chat.
        self._part_media_resolution = True

    def _url(self, model: str) -> str:
        return f"{_BASE_URL}/models/{model}:generateContent"

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        post = post_json(
            self._http,
            self._url(request.model),
            {"x-goog-api-key": self._api_key},
            _PROVIDER,
            timeout,
        )
        data, self._part_media_resolution = run_chat(
            request, post, self._part_media_resolution
        )
        return parse_response(data, request.model)
