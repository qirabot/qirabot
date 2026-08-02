"""Claude on Vertex AI via the rawPredict Messages API.

Wire semantics mirror go-llm's claude.go (Vertex branch): the model rides in
the URL instead of the body, `anthropic_version` marks the Vertex dialect,
the cacheable system block carries an explicit ephemeral cache_control, and a
second cache breakpoint lands on the last non-assistant message. Forced tool
choice is mutually exclusive with thinking (API limitation) — with thinking
on, tool_choice degrades to auto and the engine's JSON fallback catches
text-only replies.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx

from ..types import Message, TokenUsage, ToolCall, ToolDefinition
from .base import (
    ChatRequest,
    ChatResponse,
    ErrorCategory,
    ProviderError,
    http_error,
    map_finish_reason,
)
from .retry import with_retry
from .vertex_auth import VertexTokenSource, vertex_base_url

_ANTHROPIC_VERSION = "vertex-2023-10-16"
_PROVIDER = "claude-vertex"


class ClaudeVertexProvider:
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
            f"/locations/{self._location}/publishers/anthropic/models/{model}:rawPredict"
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
    """Anthropic Messages body for the Vertex dialect (no `model` field)."""
    body: dict[str, Any] = {
        "anthropic_version": _ANTHROPIC_VERSION,
        "max_tokens": request.param_int("max_tokens", 8192),
        "messages": build_messages(request.messages),
    }

    # Split system prompt: static part with cache_control, dynamic part
    # without. Anthropic prefix order: tools → system → messages; keeping the
    # system split ensures tools+system are cached together.
    if request.cacheable_system_prompt:
        system: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": request.cacheable_system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if request.system_prompt:
            system.append({"type": "text", "text": request.system_prompt})
        body["system"] = system
    elif request.system_prompt:
        body["system"] = [
            {
                "type": "text",
                "text": request.system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    temp = request.param_float("temperature", -1.0)
    if temp >= 0:
        body["temperature"] = temp
    top_p = request.param_float("top_p", -1.0)
    if top_p >= 0:
        body["top_p"] = top_p
    top_k = request.param_int("top_k", 0)
    if top_k > 0:
        body["top_k"] = top_k

    # Adaptive thinking: map the unified thinking_level to Claude effort.
    # "disabled"/"none" (or unset) sends no thinking param at all.
    effort = request.param_str("thinking_level", "")
    thinking_enabled = effort not in ("", "disabled", "none")
    if thinking_enabled:
        body["thinking"] = {"type": "adaptive"}
        body["output_config"] = {"effort": _map_effort(effort, request.model)}
        # Claude requires temperature=1 when thinking is enabled.
        body["temperature"] = 1

    if request.tools:
        body["tools"] = [_tool(t) for t in request.tools]
        # Claude does not allow forced tool_choice when thinking is enabled.
        if request.force_tool and not thinking_enabled:
            body["tool_choice"] = {"type": "any"}
        else:
            body["tool_choice"] = {"type": "auto"}

    return body


def _map_effort(effort: str, model: str) -> str:
    is_opus_47 = "opus-4-7" in model or "opus-4.7" in model
    e = effort.lower()
    if e in ("low", "minimal"):
        return "low"
    if e == "medium":
        return "medium"
    if e == "high":
        return "high"
    if e == "xhigh":
        return "xhigh" if is_opus_47 else "high"
    if e == "max":
        return "max"
    return "high"


def _tool(t: ToolDefinition) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object"}
    if t.parameters:
        props = t.parameters.get("properties")
        if props is not None:
            schema["properties"] = props
        req = t.parameters.get("required")
        if isinstance(req, list):
            schema["required"] = [r for r in req if isinstance(r, str)]
    out: dict[str, Any] = {"name": t.name, "input_schema": schema}
    if t.description:
        out["description"] = t.description
    return out


def build_messages(messages: list[Message]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    for msg in messages:
        role = "assistant" if msg.role in ("assistant", "model") else msg.role

        # Tool results -> user message with tool_result blocks
        if role == "tool" and msg.tool_results:
            blocks: list[dict[str, Any]] = [
                {
                    "type": "tool_result",
                    "tool_use_id": tr.tool_call_id,
                    "content": tr.content,
                }
                for tr in msg.tool_results
            ]
            result.append({"role": "user", "content": blocks})
            continue

        content: list[dict[str, Any]] = []
        if msg.content:
            content.append({"type": "text", "text": msg.content})
        for img in msg.images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.mime_type,
                        "data": base64.b64encode(img.data).decode("ascii"),
                    },
                }
            )
        for tc in msg.tool_calls:
            content.append(
                {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args}
            )
        if not content:
            content.append({"type": "text", "text": ""})

        result.append(
            {"role": "assistant" if role == "assistant" else "user", "content": content}
        )

    # Add cache_control to the last non-assistant message's last content
    # block: caches system prompt + tools + all messages up to this point.
    for m in reversed(result):
        if m["role"] != "assistant" and m["content"]:
            m["content"][-1]["cache_control"] = {"type": "ephemeral"}
            break

    return result


def parse_response(data: dict[str, Any], model: str) -> ChatResponse:
    resp = ChatResponse(
        finish_reason=map_finish_reason(str(data.get("stop_reason") or "")),
        model_used=model,
    )

    content = data.get("content")
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "thinking":
                text = str(block.get("thinking") or "")
                resp.thinking = f"{resp.thinking}\n{text}" if resp.thinking else text
            elif btype == "text":
                text = str(block.get("text") or "")
                resp.content = f"{resp.content}\n{text}" if resp.content else text
            elif btype == "tool_use":
                args = block.get("input")
                resp.tool_calls.append(
                    ToolCall(
                        id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        args=args if isinstance(args, dict) else {},
                    )
                )

    usage = data.get("usage")
    if isinstance(usage, dict):
        resp.token_usage = TokenUsage(
            input_tokens=int(usage.get("input_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            cache_read_tokens=int(usage.get("cache_read_input_tokens") or 0),
            cache_write_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        )

    return resp
