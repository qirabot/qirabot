"""Core data types for the local decision engine.

Mirrors the server's internal/decision/types.go plus the small structs the Go
package pulled from internal/modelalias (ModelConfig, locate-format constants)
and go-llm (Message/Image/ToolCall/ToolResult/ToolDefinition/TokenUsage), so
the whole engine package is self-contained.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class EngineError(Exception):
    """Base class for engine failures."""


class UnparsableResponseError(EngineError):
    """The LLM call succeeded but the response carried no usable action
    (no tool call and no parseable JSON — e.g. an empty Gemini
    malformed_function_call candidate). Transient by nature, so callers may
    re-decide instead of failing the step."""


class LocateUnparsableError(EngineError):
    """A locate call returned no usable report_location output. The partial
    LocateResult (token spend) is attached because the cost is real."""

    def __init__(self, message: str, result: "LocateResult") -> None:
        super().__init__(message)
        self.result = result


class UnsupportedScreenshotError(EngineError):
    """The screenshot could not be decoded; deterministic, never retried."""


# Locate coordinate dialects (mirrors internal/modelalias/alias.go).
# Unset/unknown falls back to LOCATE_FORMAT_POINT.
LOCATE_FORMAT_POINT = "point_xy_1000"
LOCATE_FORMAT_BBOX = "bbox_yx_1000"


@dataclass
class ModelConfig:
    """Resolved model configuration handed to every engine call."""

    provider: str = ""
    model: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    locate_format: str = ""


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def add(self, other: "TokenUsage | None") -> None:
        if other is None:
            return
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.thinking_tokens += other.thinking_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens


@dataclass
class Image:
    mime_type: str
    data: bytes


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    content: str


@dataclass
class Message:
    role: str
    content: str = ""
    images: list[Image] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[ToolResult] = field(default_factory=list)


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class CustomToolDef:
    """A user-registered tool definition. The engine never executes custom
    tools: they are merged into the LLM tool list, and when the model picks
    one the action is returned to the caller verbatim for local execution.
    The result comes back via the next step's action_result."""

    name: str
    description: str
    # normalized: only "properties" and "required"
    parameters: dict[str, Any] | None = None


@dataclass
class ConversationTurn:
    """A single conversation turn in history: the screenshot the model saw,
    the action it chose, the reasoning behind it, and the execution output."""

    screenshot_data: bytes = b""
    action_type: str = ""
    action_params: str = ""  # JSON-encoded params as the model emitted them
    reasoning: str = ""
    tool_output: str = ""


@dataclass
class HistoryState:
    """Serializable snapshot of History (trace/debug and tests)."""

    entries: list[ConversationTurn] = field(default_factory=list)
    summary: list[str] = field(default_factory=list)
    max_entries: int = 0
    max_screenshots: int = 0


@dataclass
class Action:
    """A single action for the automation client to execute."""

    type: str
    params: dict[str, Any] = field(default_factory=dict)
    reasoning: str = ""


@dataclass
class DecisionInput:
    """All context needed for a decision."""

    instruction: str = ""
    knowledge: str = ""  # domain background, appended to the cacheable system prompt
    platform: str = ""  # android, ios, chrome, desktop
    language: str = ""
    current_screenshot: bytes = b""
    history: list[ConversationTurn] = field(default_factory=list)
    is_first_step: bool = False
    notes: list[str] = field(default_factory=list)  # accumulated save_note contents
    summary: str = ""  # compressed summary of truncated history steps
    model_config: ModelConfig | None = None
    annotate_for_model: bool = False  # screenshots contain red crosshair markers
    correction_hint: str = ""  # corrective feedback on a re-decide; empty on first attempt
    custom_tools: list[CustomToolDef] = field(default_factory=list)
    exclude_tools: list[str] = field(default_factory=list)


@dataclass
class DecisionResult:
    action: Action | None = None
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    duration_ms: int = 0
    model_used: str = ""
    raw_response: str = ""


@dataclass
class ExtractInput:
    prompt: str = ""  # what to extract
    screenshot: bytes = b""
    platform: str = ""
    language: str = ""
    model_config: ModelConfig | None = None


@dataclass
class ExtractResult:
    result: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    duration_ms: int = 0
    model_used: str = ""


@dataclass
class LocateInput:
    locate: str = ""  # natural-language element description
    screenshot: bytes = b""
    language: str = ""
    model_config: ModelConfig | None = None


@dataclass
class LocateResult:
    """X/Y are in screenshot pixels. found=False with no error raised is the
    model's explicit "not on screen" answer; not_found_reason carries its
    explanation."""

    x: int = 0
    y: int = 0
    found: bool = False
    not_found_reason: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    duration_ms: int = 0
    model_used: str = ""


@dataclass
class ConditionInput:
    condition: str = ""  # condition to check
    screenshot: bytes = b""
    platform: str = ""
    language: str = ""
    model_config: ModelConfig | None = None


@dataclass
class ConditionResult:
    met: bool = False
    reasoning: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    duration_ms: int = 0
    model_used: str = ""


def detect_image_mime(data: bytes) -> str:
    """Detect the MIME type of image data by inspecting magic bytes.
    Falls back to "image/png" for unrecognized formats."""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if len(data) >= 8 and data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if len(data) >= 2 and data[0] == 0xFF and data[1] == 0xD8:
        return "image/jpeg"
    if len(data) >= 3 and data[:3] == b"GIF":
        return "image/gif"
    return "image/png"
