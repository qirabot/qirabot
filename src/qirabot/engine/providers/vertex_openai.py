"""OpenAI-compatible chat completions on Vertex AI (Model Garden / partner
models via the openapi endpoint).

Wire semantics mirror go-llm's vertex_openai.go, with one deliberate
addition: image parts. go-llm's vertex_openai path was text-only; every
qirabot decision carries a screenshot, so images ride as data-URI
image_url parts exactly like go-llm's openai_compat.go does for dashscope.
The model string is passed verbatim — Vertex expects a publisher-prefixed id
(e.g. "google/gemini-2.5-flash", "qwen/qwen3-vl-plus").
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx

from ..types import TokenUsage, ToolCall, ToolDefinition
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

_PROVIDER = "vertex-openai"


class VertexOpenAIProvider:
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

    def _url(self) -> str:
        return (
            f"{vertex_base_url(self._location)}/projects/{self._project}"
            f"/locations/{self._location}/endpoints/openapi/chat/completions"
        )

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        body = build_request_body(request)
        url = self._url()

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
    body: dict[str, Any] = {
        "model": request.model,
        "messages": build_messages(request),
    }

    max_tokens = request.param_int("max_tokens", 0)
    if max_tokens > 0:
        body["max_tokens"] = max_tokens
    temperature = request.param_float("temperature", 0.0)
    if temperature > 0:
        body["temperature"] = temperature
    top_p = request.param_float("top_p", 0.0)
    if top_p > 0:
        body["top_p"] = top_p

    if request.tools:
        body["tools"] = [_tool(t) for t in request.tools]
        body["tool_choice"] = "required" if request.force_tool else "auto"

    tl = request.param_str("thinking_level", "")
    if tl not in ("", "disabled", "none"):
        body["chat_template_kwargs"] = {"enable_thinking": True}

    return body


def _tool(t: ToolDefinition) -> dict[str, Any]:
    fn: dict[str, Any] = {"name": t.name, "description": t.description}
    if t.parameters:
        fn["parameters"] = t.parameters
    return {"type": "function", "function": fn}


def _data_uri(mime_type: str, data: bytes) -> str:
    return f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"


def build_messages(request: ChatRequest) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []

    system = request.cacheable_system_prompt + request.system_prompt
    if system:
        result.append({"role": "system", "content": system})

    for msg in request.messages:
        role = "assistant" if msg.role == "model" else msg.role

        # Tool results -> individual role=tool messages
        if role == "tool" and msg.tool_results:
            for tr in msg.tool_results:
                result.append(
                    {"role": "tool", "tool_call_id": tr.tool_call_id, "content": tr.content}
                )
            continue

        m: dict[str, Any] = {"role": role}

        if msg.images:
            parts: list[dict[str, Any]] = []
            if msg.content:
                parts.append({"type": "text", "text": msg.content})
            for img in msg.images:
                parts.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": _data_uri(img.mime_type, img.data)},
                    }
                )
            m["content"] = parts
        else:
            m["content"] = msg.content

        if msg.tool_calls:
            m["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.args, ensure_ascii=False),
                    },
                }
                for tc in msg.tool_calls
            ]

        result.append(m)

    return result


def parse_response(data: dict[str, Any], model: str) -> ChatResponse:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ChatResponse(finish_reason="stop", model_used=model)

    choice = choices[0]
    message = choice.get("message")
    message = message if isinstance(message, dict) else {}

    resp = ChatResponse(
        content=str(message.get("content") or ""),
        thinking=str(message.get("reasoning_content") or ""),
        finish_reason=map_finish_reason(str(choice.get("finish_reason") or "")),
        model_used=model,
    )

    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function")
            if not isinstance(fn, dict):
                continue
            args: dict[str, Any] = {}
            raw_args = fn.get("arguments")
            if isinstance(raw_args, str) and raw_args:
                try:
                    parsed = json.loads(raw_args)
                    if isinstance(parsed, dict):
                        args = parsed
                    else:
                        args = {"raw": raw_args}
                except ValueError:
                    args = {"raw": raw_args}
            resp.tool_calls.append(
                ToolCall(
                    id=str(tc.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    args=args,
                )
            )

    usage = data.get("usage")
    if isinstance(usage, dict):
        resp.token_usage = _usage(usage)

    return resp


def _usage(u: dict[str, Any]) -> TokenUsage:
    def n(d: dict[str, Any], key: str) -> int:
        v = d.get(key)
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else 0

    completion_details = u.get("completion_tokens_details")
    reasoning = (
        n(completion_details, "reasoning_tokens")
        if isinstance(completion_details, dict)
        else 0
    )
    prompt_details = u.get("prompt_tokens_details")
    cached = cache_write = 0
    if isinstance(prompt_details, dict):
        cached = n(prompt_details, "cache_read_tokens") + n(prompt_details, "cached_tokens")
        cache_write = n(prompt_details, "cache_creation_tokens") + n(
            prompt_details, "cache_creation_input_tokens"
        )

    return TokenUsage(
        input_tokens=n(u, "prompt_tokens"),
        output_tokens=n(u, "completion_tokens"),
        thinking_tokens=reasoning,
        cache_read_tokens=cached,
        cache_write_tokens=cache_write,
    )
