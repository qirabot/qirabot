"""Shared generateContent wire format for the two Gemini transports.

Both providers — gemini_vertex (Vertex AI, ADC or Vertex API key) and
gemini_api (Gemini Developer API, AI Studio key) — speak the identical
generateContent dialect; only URL and auth differ. Everything wire-shaped
lives here so the transports stay thin and neither sibling imports the other.

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
import logging
import uuid
from typing import Any, Callable

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

logger = logging.getLogger("qirabot.engine")

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


def post_json(
    http: httpx.Client,
    url: str,
    headers: dict[str, str] | Callable[[], dict[str, str]],
    provider: str,
    timeout: float,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """A ``post(body) -> response dict`` callable for :func:`run_chat`.

    POSTs JSON and classifies transport failures under ``provider``'s name.
    ``headers`` may be a dict or a zero-arg callable — Vertex bearer tokens
    refresh per request, API keys are static.
    """

    def post(body: dict[str, Any]) -> dict[str, Any]:
        hdrs = headers() if callable(headers) else headers
        try:
            resp = http.post(url, json=body, headers=hdrs, timeout=timeout)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                provider, f"request timed out: {exc}", category=ErrorCategory.TIMEOUT
            ) from exc
        if resp.status_code != 200:
            raise http_error(provider, resp.status_code, resp.text)
        data = resp.json()
        if not isinstance(data, dict):
            raise ProviderError(provider, "non-object response body")
        return data

    return post


def run_chat(
    request: ChatRequest,
    post: Any,
    part_media_resolution: bool,
) -> tuple[dict[str, Any], bool]:
    """Build the body and POST it (post: callable(body) -> response dict),
    falling back once when the endpoint rejects per-part mediaResolution
    (older models/endpoints): the offending part fields are stripped and the
    request re-sent. Returns (response, whether per-part mediaResolution is
    still believed supported) — callers keep that flag per provider instance
    so at most one request is ever wasted on the probe."""
    body = build_request_body(request, part_media_resolution=part_media_resolution)
    try:
        return with_retry(lambda: post(body)), part_media_resolution
    except ProviderError as exc:
        fallback = (
            strip_part_media_resolution(body)
            if rejects_part_media_resolution(exc)
            else None
        )
        if fallback is None:
            raise
        logger.warning(
            "endpoint rejected per-part mediaResolution; retrying without it "
            "and disabling it for this provider (model=%s)",
            request.model,
        )
        return with_retry(lambda: post(fallback)), False


def rejects_part_media_resolution(exc: Exception) -> bool:
    """True when the error is the endpoint refusing the part-level
    mediaResolution field (unknown field or invalid value, always a 400
    naming the part path) — as opposed to any other invalid-request cause."""
    if not isinstance(exc, ProviderError) or exc.status_code != 400:
        return False
    msg = str(exc)
    return "parts[" in msg and ("media_resolution" in msg or "mediaResolution" in msg)


def strip_part_media_resolution(body: dict[str, Any]) -> dict[str, Any] | None:
    """Copy of body without part-level mediaResolution fields; None when the
    body carried none (then the field cannot be what the endpoint rejected)."""
    stripped = False
    contents: list[dict[str, Any]] = []
    for c in body.get("contents", []):
        parts: list[dict[str, Any]] = []
        for p in c.get("parts", []):
            if "mediaResolution" in p:
                p = {k: v for k, v in p.items() if k != "mediaResolution"}
                stripped = True
            parts.append(p)
        contents.append({**c, "parts": parts})
    if not stripped:
        return None
    return {**body, "contents": contents}


def build_request_body(
    request: ChatRequest, part_media_resolution: bool = True
) -> dict[str, Any]:
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

    global_media_resolution = ""
    media_resolution = request.param_str("media_resolution", "")
    if media_resolution:
        global_media_resolution = _map_media_resolution(media_resolution)
        generation_config["mediaResolution"] = global_media_resolution

    body: dict[str, Any] = {
        "contents": build_contents(
            request.messages,
            global_media_resolution=global_media_resolution,
            part_media_resolution=part_media_resolution,
        ),
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


def _map_media_resolution(value: str) -> str:
    # Same table (and same fallback-to-medium on unknown values) as go-llm.
    return {
        "low": "MEDIA_RESOLUTION_LOW",
        "medium": "MEDIA_RESOLUTION_MEDIUM",
        "high": "MEDIA_RESOLUTION_HIGH",
        "ultra_high": "MEDIA_RESOLUTION_ULTRA_HIGH",
    }.get(value.lower(), "MEDIA_RESOLUTION_MEDIUM")


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


def build_contents(
    messages: list[Message],
    global_media_resolution: str = "",
    part_media_resolution: bool = False,
) -> list[dict[str, Any]]:
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
            part: dict[str, Any] = {
                "inlineData": {
                    "mimeType": img.mime_type,
                    "data": base64.b64encode(img.data).decode("ascii"),
                }
            }
            # Per-image override, emitted only when it differs from the
            # request-level resolution — a redundant field would just risk a
            # rejection on endpoints without per-part support.
            if part_media_resolution and img.resolution:
                level = _map_media_resolution(img.resolution)
                if level != global_media_resolution:
                    part["mediaResolution"] = {"level": level}
            parts.append(part)
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
