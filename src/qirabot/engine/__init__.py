"""Local decision engine: prompts, tool registry, history and coordinate
logic ported from the qirabot-v2 server's internal/decision package.

The LLM-facing entry points (Engine, LocalBackend) land in later stages; this
package level re-exports the stable data types.
"""

from .types import (
    LOCATE_FORMAT_BBOX,
    LOCATE_FORMAT_POINT,
    Action,
    ConditionInput,
    ConditionResult,
    ConversationTurn,
    CustomToolDef,
    DecisionInput,
    DecisionResult,
    EngineError,
    ExtractInput,
    ExtractResult,
    HistoryState,
    LocateInput,
    LocateResult,
    LocateUnparsableError,
    Message,
    ModelConfig,
    TokenUsage,
    ToolDefinition,
    UnparsableResponseError,
    UnsupportedScreenshotError,
)

__all__ = [
    "LOCATE_FORMAT_BBOX",
    "LOCATE_FORMAT_POINT",
    "Action",
    "ConditionInput",
    "ConditionResult",
    "ConversationTurn",
    "CustomToolDef",
    "DecisionInput",
    "DecisionResult",
    "EngineError",
    "ExtractInput",
    "ExtractResult",
    "HistoryState",
    "LocateInput",
    "LocateResult",
    "LocateUnparsableError",
    "Message",
    "ModelConfig",
    "TokenUsage",
    "ToolDefinition",
    "UnparsableResponseError",
    "UnsupportedScreenshotError",
]
