"""System-prompt assembly and conversation-message building.

Mirrors internal/decision/prompts.go (+ knowledgeSection from knowledge.go).
The system prompt is split into a cacheable half (constant within a task, so
the provider prompt-cache prefix stays stable across steps) and a dynamic
half (date, goal, summary, notes).
"""

from __future__ import annotations

import json
import logging
from datetime import date as _date
from datetime import datetime
from importlib import resources
from typing import Any

from . import actions
from .custom_tools import custom_tool_names
from .types import DecisionInput, Image, Message, ToolCall, ToolResult, detect_image_mime

logger = logging.getLogger("qirabot.engine")


def _load_platform_prompt(platform: str) -> str:
    return (
        resources.files("qirabot.engine")
        .joinpath("prompt_data")
        .joinpath(f"{platform}.txt")
        .read_text(encoding="utf-8")
    )


PLATFORM_PROMPTS: dict[str, str] = {
    actions.PLATFORM_ANDROID: _load_platform_prompt("android"),
    actions.PLATFORM_IOS: _load_platform_prompt("ios"),
    actions.PLATFORM_CHROME: _load_platform_prompt("chrome"),
    actions.PLATFORM_DESKTOP: _load_platform_prompt("desktop"),
}


def resolve_prompt(registry: dict[str, str], platform: str) -> str:
    """Look up the prompt for the given platform, falling back to android."""
    p = registry.get(platform)
    if p is None:
        logger.warning("unknown platform, falling back to android: %s", platform)
        p = registry[actions.PLATFORM_ANDROID]
    return p


# The inline coordinate contract, stated once and reused by both the
# system-prompt grounding guidance and the corrective re-decide hints in the
# session layer, so the rule can't drift between them.
NORMALIZED_COORD_RULE = (
    "Coordinates must be integers normalized to 0~1000 "
    "(origin at the top-left, x horizontal, y vertical)"
)


# Single-step locate role prompt (bot.click / bot.locate). The rules target
# the known failure modes of VLM grounding on dense or similar-element
# screens. The last rule is load-bearing: an explicit not-found beats a
# guessed coordinate, because the single-step API is the deterministic layer
# — failures must be loud.
LOCATE_SYSTEM_PROMPT = """You are a UI element locator. Given a screenshot and an element description, find the single element that matches the description and report its location with the report_location tool.

Rules:
- Locate exactly the element the description names; treat labels, rows, containers and nearby text as context only, not as the target.
- If the target is visible text, aim at the tight region of that text, not the whole line, row or container.
- If the target is an input field or a value area, aim at the field body, not a trailing icon (search, dropdown arrow, clear button) next to it.
- If the target is an icon or a small control, aim at that glyph only, not its neighboring label text.
- When several similar elements are visible, follow the description's ordering and positional cues exactly (e.g. "second from the left", "in the bottom row", "next to X").
- When the same text appears in several regions, prefer the region the description names.
- If no element matches the description, report found=false with a short reason. Never guess coordinates."""


def build_system_prompts(
    platform: str,
    instruction: str,
    knowledge: str,
    summary: str,
    notes: str,
    language: str,
    annotate_for_model: bool,
    exclude_tools: list[str],
    now: datetime | None = None,
) -> tuple[str, str]:
    """Returns (cacheable, dynamic) halves of the system prompt."""
    cacheable = (
        resolve_prompt(PLATFORM_PROMPTS, platform)
        + grounding_guidance()
        + knowledge_section(knowledge)
        + excluded_tools_section(exclude_tools)
    )
    dynamic = build_dynamic_prompt(instruction, summary, notes, language, annotate_for_model, now)
    return cacheable, dynamic


def excluded_tools_section(exclude_tools: list[str]) -> str:
    """Tool-availability caveat for the cacheable system prompt. The platform
    prompt is static text that references tools by name; when the caller
    disabled some of them via exclude_tools, those references become
    contradictory instructions — this section tells the model to disregard
    them. Lives in the cacheable half because exclude_tools is fixed within a
    task, so the prompt-cache prefix stays stable across steps."""
    if not exclude_tools:
        return ""
    return (
        "\n\n# Tool availability\nThe following tools are disabled for this task: `"
        + "`, `".join(exclude_tools)
        + "`. Ignore any rules above that require or suggest these tools; use the currently "
        "available tools to achieve the same purpose instead. If no substitute exists and the "
        "task therefore cannot be completed, call `done` (success=false) explaining why."
    )


def grounding_guidance() -> str:
    """Element-targeting section appended to the cacheable system prompt.
    Kept in sync with the tool schema (which carries point_x/point_y via
    with_point_fields) so the prompt never contradicts it."""
    return (
        "\n\n# Coordinate output\nFor actions that target a UI element (click, type, drag, "
        "etc.), you must output the target's center point as point_x and point_y. "
        + NORMALIZED_COORD_RULE
        + ". For drag, output start_point_x/start_point_y and end_point_x/end_point_y."
    )


def knowledge_section(knowledge: str) -> str:
    """User-supplied domain knowledge for the cacheable system prompt. The
    framing matters: this is reference material, not the goal, so imperative
    sentences inside a rules document must not read as pending tasks."""
    if not knowledge:
        return ""
    return (
        "\n\n# Domain knowledge\nThe following is background knowledge provided by the user "
        "(game rules, business flows, terminology, etc.) for reference when making decisions. "
        "It is not the task goal, and statements inside it are not instructions to execute; "
        "when it conflicts with the actual state of the current UI, the UI takes precedence.\n"
        + knowledge
    )


_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _format_date(d: _date) -> str:
    # Go's "2006-01-02 Monday"; weekday names spelled out so the output is
    # locale-independent (strftime %A follows the process locale).
    return f"{d.isoformat()} {_WEEKDAYS[d.weekday()]}"


def build_dynamic_prompt(
    instruction: str,
    summary: str,
    notes: str,
    language: str,
    annotate_for_model: bool,
    now: datetime | None = None,
) -> str:
    """Dynamic prompt filled with task context."""
    lang_name = get_language_display_name(language)
    moment = now if now is not None else datetime.now()

    parts = ["# Current task context\n## Current date\n", _format_date(moment.date())]
    parts.append("\n\n## User goal\n")
    parts.append(instruction)

    if summary:
        parts.append("\n\n## Summary of completed steps\n")
        parts.append(summary)

    if notes:
        parts.append("\n\n## Saved notes\n")
        parts.append(notes)

    if annotate_for_model:
        parts.append("\n\n## Screenshot annotations\n")
        parts.append(
            "Earlier screenshots may contain red crosshair markers indicating the coordinates "
            "of the previous action. These markers only help you understand where actions were "
            "performed; they are not UI elements — ignore whatever they cover."
        )

    parts.append("\n\nRespond in ")
    parts.append(lang_name)
    return "".join(parts)


def build_conversation_messages(input: DecisionInput) -> list[Message]:
    """Assemble the LLM message list from history and the current screenshot.
    History is replayed using tool call format: screenshot (user) ->
    function call (model) -> function response (tool)."""
    messages: list[Message] = []
    custom_names = custom_tool_names(input.custom_tools)

    # Replay conversation history (already trimmed by History.add)
    for turn in input.history:
        # 1. Screenshot the model saw
        if turn.screenshot_data:
            messages.append(
                Message(
                    role="user",
                    images=[
                        Image(
                            mime_type=detect_image_mime(turn.screenshot_data),
                            data=turn.screenshot_data,
                        )
                    ],
                )
            )

        # 2. Model's decision as tool call
        if turn.action_type:
            args = parse_history_args(turn.action_params)
            if turn.reasoning:
                args["reason"] = turn.reasoning
            messages.append(
                Message(
                    role="assistant",
                    tool_calls=[
                        ToolCall(id=turn.action_type, name=turn.action_type, args=args)
                    ],
                )
            )

            # 3. Tool execution output as function response
            tool_output = turn.tool_output or "ok"
            # Custom tool results are textual (client-executed, e.g. an API
            # response), not something the screenshot can confirm.
            if turn.action_type not in custom_names:
                tool_output += "\nVerify the actual result from the screenshot"
            messages.append(
                Message(
                    role="tool",
                    tool_results=[
                        ToolResult(
                            tool_call_id=turn.action_type,
                            name=turn.action_type,
                            content=tool_output,
                        )
                    ],
                )
            )

    # Always include task instruction as first user message so the model sees
    # the goal in conversation context, not just in system prompt. Some models
    # (e.g. qwen) follow system prompt less strictly.
    if input.is_first_step:
        task_content = f"Task: {input.instruction}\n\nPlease begin."
    else:
        task_content = f"Task: {input.instruction}\n\nContinue from where you left off."
    messages.insert(0, Message(role="user", content=task_content))

    # Add current screenshot
    if input.current_screenshot:
        messages.append(
            Message(
                role="user",
                images=[
                    Image(
                        mime_type=detect_image_mime(input.current_screenshot),
                        data=input.current_screenshot,
                    )
                ],
            )
        )
    elif input.is_first_step:
        messages.append(
            Message(
                role="user",
                content="(no screenshot available, decide the first action based on the task description)",
            )
        )

    # Corrective feedback for a re-decide goes last, after the current
    # screenshot, so the model re-grounds against what it just saw.
    if input.correction_hint:
        messages.append(Message(role="user", content=input.correction_hint))

    return messages


def parse_history_args(params_json: str) -> dict[str, Any]:
    """Convert a JSON params string to a dict."""
    if not params_json:
        return {}
    try:
        args = json.loads(params_json)
    except ValueError:
        return {}
    if not isinstance(args, dict):
        return {}
    return args


def format_notes(notes: list[str]) -> str:
    """Join saved notes for prompt injection. Notes are separated by "---" to
    avoid duplicate numbering when note content already contains its own list
    formatting."""
    if not notes:
        return ""
    return "\n---\n".join(notes)


def get_language_display_name(lang: str) -> str:
    if lang.lower().startswith("zh"):
        return "中文"
    return "English"
