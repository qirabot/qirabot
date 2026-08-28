"""System-prompt assembly and conversation-message building.

The step summary and saved notes deliberately stay OUT of the system prompt:
it sits at the head of the token stream, so every save_note or window
truncation would change it and invalidate the whole provider prompt-cache
prefix. The system prompt is constant within a task, and summary/notes
travel as a progress-context message near the tail of the conversation (see
build_conversation_messages), so the cache prefix survives across steps.
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
from .tools import response_language
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


def build_system_prompt(
    platform: str,
    instruction: str,
    knowledge: str,
    language: str,
    annotate_for_model: bool,
    exclude_tools: list[str],
    now: datetime | None = None,
) -> str:
    """The system prompt, constant within a task — anything that changes
    step to step (summary, notes) belongs in progress_context_section, not
    here. Section order is deliberate: the sections shared across tasks on the
    same platform (role, grounding) come first and the task-specific ones
    (knowledge, excluded tools, instruction/date) last, keeping the longest
    possible byte-stable head for provider prompt caches."""
    return (
        resolve_prompt(PLATFORM_PROMPTS, platform)
        + grounding_guidance()
        + knowledge_section(knowledge)
        + excluded_tools_section(exclude_tools)
        + build_dynamic_prompt(instruction, language, annotate_for_model, now)
    )


def excluded_tools_section(exclude_tools: list[str]) -> str:
    """Tool-availability caveat for the system prompt. The platform prompt is
    static text that references tools by name; when the caller disabled some
    of them via exclude_tools, those references become contradictory
    instructions — this section tells the model to disregard them.
    exclude_tools is fixed within a task, so the section never perturbs the
    prompt-cache prefix across steps."""
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
    """Element-targeting section of the system prompt.
    Kept in sync with the tool schema (which carries point_x/point_y via
    with_point_fields) so the prompt never contradicts it."""
    return (
        "\n\n# Coordinate output\nFor actions that target a UI element (click, type, drag, "
        "etc.), you must output the target's center point as point_x and point_y. "
        + NORMALIZED_COORD_RULE
        + ". For drag, output start_point_x/start_point_y and end_point_x/end_point_y."
    )


def knowledge_section(knowledge: str) -> str:
    """User-supplied domain knowledge section of the system prompt. The
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
    # "2026-01-02 Monday" — weekday names spelled out so the output is
    # locale-independent (strftime %A follows the process locale).
    return f"{d.isoformat()} {_WEEKDAYS[d.weekday()]}"


def build_dynamic_prompt(
    instruction: str,
    language: str,
    annotate_for_model: bool,
    now: datetime | None = None,
) -> str:
    """Dynamic prompt filled with task context. Everything here is constant
    for the duration of a task (the date can flip at midnight — accepted)."""
    lang_name = response_language(language)
    moment = now if now is not None else datetime.now()

    parts = ["# Current task context\n## Current date\n", _format_date(moment.date())]
    parts.append("\n\n## User goal\n")
    parts.append(instruction)

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


def progress_context_section(summary: str, notes: str) -> str:
    """Step summary + saved notes as a conversation message body. Lives near
    the TAIL of the contents (after the history triads, before the current
    screenshot) because it changes during the task — putting it in the system
    prompt would invalidate the provider cache prefix on every change. Section
    headings match the ones the platform prompts reference ("completed-steps
    summary", "saved notes")."""
    if not summary and not notes:
        return ""
    parts = ["# Progress context"]
    if summary:
        parts.append("\n\n## Summary of completed steps\n" + summary)
    if notes:
        parts.append("\n\n## Saved notes\n" + notes)
    return "".join(parts)


def build_conversation_messages(input: DecisionInput) -> list[Message]:
    """Assemble the LLM message list from history and the current screenshot.
    History is replayed using tool call format: screenshot (user) ->
    function call (model) -> function response (tool).

    Ordering is cache-aware: [task, triads oldest→newest, progress context,
    current screenshot, correction hint]. Everything up to the second-newest
    triad is byte-stable across steps (triad text never changes once rendered;
    only the attached screenshot hops forward), so the provider prefix cache
    covers the system prompt, tools and the whole text history."""
    messages: list[Message] = []
    custom_names = custom_tool_names(input.custom_tools)

    # Replay conversation history (already trimmed by History.add)
    for turn in input.history:
        # 1. Screenshot the model saw. Always low resolution: history images
        # only answer "did the UI change as expected" — grounding and reading
        # happen on the current screenshot, which follows the task setting.
        if turn.screenshot_data:
            messages.append(
                Message(
                    role="user",
                    images=[
                        Image(
                            mime_type=detect_image_mime(turn.screenshot_data),
                            data=turn.screenshot_data,
                            resolution="low",
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
    # the goal in conversation context, not just in the system prompt, which
    # some models follow less strictly.
    if input.is_first_step:
        task_content = f"Task: {input.instruction}\n\nPlease begin."
    else:
        task_content = f"Task: {input.instruction}\n\nContinue from where you left off."
    messages.insert(0, Message(role="user", content=task_content))

    # Mutable task context (summary/notes) goes after the stable history
    # block, right before the current screenshot.
    progress = progress_context_section(input.summary, format_notes(input.notes))
    if progress:
        messages.append(Message(role="user", content=progress))

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
