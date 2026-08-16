"""Provider protocol, wire-neutral request/response types and error taxonomy.

Mirrors the slices of go-llm the decision engine actually uses: a single
non-streaming chat call with tools, images and a split system prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from ..types import Message, TokenUsage, ToolCall, ToolDefinition


class ErrorCategory(str, Enum):
    """Provider-neutral classification of an LLM call failure."""

    INTERNAL = "internal"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    INVALID_REQUEST = "invalid_request"
    CONTENT_BLOCKED = "content_blocked"
    AUTH = "auth"
    UNAVAILABLE = "unavailable"


# Categories where a second attempt can plausibly succeed. Narrower than
# go-llm's retry-everything: deterministic failures (400/401/403/404) never
# get retried locally — the user pays for every attempt.
#
# TIMEOUT is deliberately absent. It means the request reached the model and
# the answer did not come back inside the budget — a slow or queued call, not
# a blip. Each retry costs another full budget (minutes at the engine's
# per-call timeouts) to ask a question that was already too slow once.
# Failures that never reached the model — connect, pool, reset — are
# classified UNAVAILABLE instead and stay retryable.
RETRYABLE_CATEGORIES = frozenset(
    {ErrorCategory.RATE_LIMITED, ErrorCategory.UNAVAILABLE}
)


class ProviderError(Exception):
    """Wraps a provider failure with a neutral category and the originating
    HTTP status code (0 if unknown). The message keeps full detail for local
    logs and reports."""

    def __init__(
        self,
        provider: str,
        message: str,
        category: ErrorCategory = ErrorCategory.INTERNAL,
        status_code: int = 0,
    ) -> None:
        super().__init__(f"{provider} api call [{category.value}/{status_code}]: {message}")
        self.provider = provider
        self.category = category
        self.status_code = status_code

    @property
    def retryable(self) -> bool:
        return self.category in RETRYABLE_CATEGORIES


def classify_http_status(code: int) -> ErrorCategory:
    """Map an HTTP status code to a neutral ErrorCategory (same table as
    go-llm's classifyHTTPStatus)."""
    if code == 429:
        return ErrorCategory.RATE_LIMITED
    if code in (408, 504):
        return ErrorCategory.TIMEOUT
    if code in (400, 404, 422):
        return ErrorCategory.INVALID_REQUEST
    if code in (401, 403):
        return ErrorCategory.AUTH
    if code >= 500:
        return ErrorCategory.UNAVAILABLE
    return ErrorCategory.INTERNAL


def http_error(provider: str, status: int, body: str) -> ProviderError:
    """Build a ProviderError from a known HTTP status and raw response body."""
    return ProviderError(
        provider,
        f"api request failed: status {status}, body: {body}",
        category=classify_http_status(status),
        status_code=status,
    )


@dataclass
class ChatRequest:
    """A single conversation call (go-llm ConversationRequest subset)."""

    model: str = ""
    messages: list[Message] = field(default_factory=list)
    tools: list[ToolDefinition] = field(default_factory=list)
    force_tool: bool = False
    # Split system prompt: the cacheable half is constant within a task (gets
    # an explicit cache breakpoint on Claude; concatenated elsewhere).
    cacheable_system_prompt: str = ""
    system_prompt: str = ""
    params: dict[str, Any] = field(default_factory=dict)

    # -- typed param accessors (params carry model-alias style values) --

    def param_int(self, key: str, default: int) -> int:
        v = self.params.get(key)
        if isinstance(v, bool):
            return default
        if isinstance(v, (int, float)):
            return int(v)
        return default

    def param_float(self, key: str, default: float) -> float:
        v = self.params.get(key)
        if isinstance(v, bool):
            return default
        if isinstance(v, (int, float)):
            return float(v)
        return default

    def param_str(self, key: str, default: str) -> str:
        v = self.params.get(key)
        return v if isinstance(v, str) else default


# Finish reason constants (normalized across providers).
FINISH_STOP = "stop"
FINISH_LENGTH = "length"
FINISH_TOOL_CALLS = "tool_calls"
FINISH_SAFETY = "safety"


def map_finish_reason(reason: str) -> str:
    """Normalize provider finish reasons (same table as go-llm)."""
    r = reason.lower()
    if r in ("stop", "end_turn", "stop_sequence"):
        return FINISH_STOP
    if r in ("length", "max_tokens"):
        return FINISH_LENGTH
    if r in ("tool_calls", "tool_use"):
        return FINISH_TOOL_CALLS
    if r in ("safety", "recitation", "blocklist", "prohibited_content", "spii", "content_filter"):
        return FINISH_SAFETY
    if reason == "":
        return FINISH_STOP
    return reason


@dataclass
class ChatResponse:
    """Unified provider response (go-llm LLMResponse subset)."""

    content: str = ""
    thinking: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = ""
    model_used: str = ""
    # Consumption tier the endpoint actually served, in its own vocabulary
    # (Vertex: usageMetadata.trafficType). Empty when not reported.
    traffic_type: str = ""


class Provider(Protocol):
    """A chat-capable LLM provider."""

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse: ...
