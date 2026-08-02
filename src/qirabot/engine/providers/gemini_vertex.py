"""Gemini on Vertex AI via the generateContent REST API.

Wire semantics mirror go-llm's gemini.go: system instruction is the
concatenated cacheable+dynamic prompt (Gemini caches implicitly), tool
schemas are reduced to type/description/enum/properties/required (Gemini's
schema dialect rejects the rest), forced tool calling uses mode ANY, safety
filters are off (screenshots of arbitrary UIs trip them constantly), and a
functionResponse part must directly follow its functionCall part — the
engine's tool-triad replay guarantees that ordering.
"""

from __future__ import annotations

import base64
import uuid
from typing import Any

import httpx

from ..types import Message, TokenUsage, ToolCall, ToolDefinition
from .base import (
    FINISH_SAFETY,
    ChatRequest,
    ChatResponse,
    ErrorCategory,
    ProviderError,
    http_error,
    map_finish_reason,
)
from .retry import with_retry
from .vertex_auth import VertexTokenSource, vertex_base_url

_PROVIDER = "gemini-vertex"

_SAFETY_OFF = [
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "OFF"},
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "OFF"},
]

_SCHEMA_TYPES = {
    "string": "STRING",
    "integer": "INTEGER",
    "number": "NUMBER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}


class GeminiVertexProvider:
    def __init__(
        self,
        project: str,
        location: str,
        token_source: VertexTokenSource,
        http_client: httpx.Client,
    ) -> None:
        self._project = project
        self._location = location
        self._tokens = token_source
        self._http = http_client

    def _url(self, model: str) -> str:
        return (
            f"{vertex_base_url(self._location)}/projects/{self._project}"
            f"/locations/{self._location}/publishers/google/models/{model}:generateContent"
        )

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        body = build_request_body(request)
        url = self._url(request.model)

        def call() -> dict[str, Any]:
            token = self._tokens.token()
            try:
                resp = self._http.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {token}"},
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


def build_request_body(request: ChatRequest) -> dict[str, Any]:
    generation_config: dict[str, Any] = {
        "temperature": request.param_float("temperature", 0.0),
        "topP": request.param_float("top_p", 0.0),
        # NOTE: reads max_output_tokens (not max_tokens), faithfully mirroring
        # go-llm — the engine's max_tokens default never applied to Gemini.
        "maxOutputTokens": request.param_int("max_output_tokens", 8192),
    }

    # Gemini 3 models cannot fully disable thinking: "disabled"/"none" clamps
    # to the model's minimum level (pro → LOW, flash/flash-lite → MINIMAL).
    thinking_level = request.param_str("thinking_level", "")
    if thinking_level:
        generation_config["thinkingConfig"] = {
            "thinkingLevel": _map_thinking_level(thinking_level, request.model)
        }

    body: dict[str, Any] = {
        "contents": build_contents(request.messages),
        "generationConfig": generation_config,
        "safetySettings": _SAFETY_OFF,
    }

    full_system = request.cacheable_system_prompt + request.system_prompt
    if full_system:
        body["systemInstruction"] = {"parts": [{"text": full_system}]}

    if request.tools:
        body["tools"] = [{"functionDeclarations": [_declaration(t) for t in request.tools]}]
        body["toolConfig"] = {
            "functionCallingConfig": {"mode": "ANY" if request.force_tool else "AUTO"}
        }

    return body


def _map_thinking_level(level: str, model: str) -> str:
    is_pro = "-pro" in model
    lv = level.lower()
    if lv in ("disabled", "none", "minimal"):
        return "LOW" if is_pro else "MINIMAL"
    if lv == "low":
        return "LOW"
    if lv == "medium":
        return "MEDIUM"
    return "HIGH"  # high / xhigh / max / unknown


def _declaration(t: ToolDefinition) -> dict[str, Any]:
    decl: dict[str, Any] = {"name": t.name, "description": t.description}
    if t.parameters:
        decl["parameters"] = _schema(t.parameters)
    return decl


def _schema(m: dict[str, Any]) -> dict[str, Any]:
    """Reduce a JSON Schema dict to the subset Gemini accepts:
    type/description/enum/properties/required — everything else is dropped."""
    s: dict[str, Any] = {}
    t = m.get("type")
    if isinstance(t, str):
        s["type"] = _SCHEMA_TYPES.get(t, "STRING")
    desc = m.get("description")
    if isinstance(desc, str):
        s["description"] = desc
    enum = m.get("enum")
    if isinstance(enum, list):
        s["enum"] = [e for e in enum if isinstance(e, str)]
    props = m.get("properties")
    if isinstance(props, dict):
        s["properties"] = {
            k: _schema(v) for k, v in props.items() if isinstance(v, dict)
        }
    req = m.get("required")
    if isinstance(req, list):
        s["required"] = [r for r in req if isinstance(r, str)]
    return s


def build_contents(messages: list[Message]) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []

    for msg in messages:
        # Tool results -> functionResponse parts (role=user in Gemini)
        if msg.role == "tool" and msg.tool_results:
            parts: list[dict[str, Any]] = [
                {
                    "functionResponse": {
                        "name": tr.name,
                        "response": {"output": tr.content},
                    }
                }
                for tr in msg.tool_results
            ]
            contents.append({"role": "user", "parts": parts})
            continue

        role = "model" if msg.role in ("model", "assistant") else "user"
        parts = []
        for img in msg.images:
            parts.append(
                {
                    "inlineData": {
                        "mimeType": img.mime_type,
                        "data": base64.b64encode(img.data).decode("ascii"),
                    }
                }
            )
        if msg.content:
            parts.append({"text": msg.content})
        for tc in msg.tool_calls:
            parts.append({"functionCall": {"name": tc.name, "args": tc.args}})

        if parts:
            contents.append({"role": role, "parts": parts})

    return contents


def parse_response(data: dict[str, Any], model: str) -> ChatResponse:
    candidates = data.get("candidates")
    finish_reason = ""
    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        finish_reason = str(candidates[0].get("finishReason") or "")

    resp = ChatResponse(
        token_usage=_usage(data),
        finish_reason=map_finish_reason(finish_reason),
        model_used=model,
    )

    feedback = data.get("promptFeedback")
    if isinstance(feedback, dict) and feedback.get("blockReason"):
        resp.finish_reason = FINISH_SAFETY

    if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
        content = candidates[0].get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                text = part.get("text")
                if part.get("thought") and isinstance(text, str) and text:
                    resp.thinking = f"{resp.thinking}\n{text}" if resp.thinking else text
                    continue
                if isinstance(text, str) and text:
                    resp.content += text
                fc = part.get("functionCall")
                if isinstance(fc, dict):
                    args = fc.get("args")
                    resp.tool_calls.append(
                        ToolCall(
                            id=f"call_{uuid.uuid4()}",
                            name=str(fc.get("name") or ""),
                            args=args if isinstance(args, dict) else {},
                        )
                    )

    return resp


def _usage(data: dict[str, Any]) -> TokenUsage:
    meta = data.get("usageMetadata")
    if not isinstance(meta, dict):
        return TokenUsage()

    def n(key: str) -> int:
        v = meta.get(key)
        return int(v) if isinstance(v, (int, float)) else 0

    prompt = n("promptTokenCount")
    cached = n("cachedContentTokenCount")
    candidates = n("candidatesTokenCount")
    thoughts = n("thoughtsTokenCount")
    return TokenUsage(
        input_tokens=prompt - cached,
        output_tokens=candidates + thoughts,
        thinking_tokens=thoughts,
        cache_read_tokens=cached,
    )
