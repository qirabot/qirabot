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
NORMALIZED_COORD_RULE = "坐标必须归一化到 0~1000 的整数（原点左上，x 水平，y 垂直）"


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
        "\n\n# 工具可用性\n本任务已禁用以下工具：`"
        + "`、`".join(exclude_tools)
        + "`。上文中要求或建议使用这些工具的规则一律忽略，改用当前可用的工具达成同等目的；"
        "若无可替代工具且任务因此无法完成，调用`done`（success=false）说明原因。"
    )


def grounding_guidance() -> str:
    """Element-targeting section appended to the cacheable system prompt.
    Kept in sync with the tool schema (which carries point_x/point_y via
    with_point_fields) so the prompt never contradicts it."""
    return (
        "\n\n# 坐标输出\n对于点击、输入、拖拽等针对界面元素的操作，必须输出目标中心点 point_x、point_y。"
        + NORMALIZED_COORD_RULE
        + "。drag 输出 start_point_x/start_point_y 与 end_point_x/end_point_y。"
    )


def knowledge_section(knowledge: str) -> str:
    """User-supplied domain knowledge for the cacheable system prompt. The
    framing matters: this is reference material, not the goal, so imperative
    sentences inside a rules document must not read as pending tasks."""
    if not knowledge:
        return ""
    return (
        "\n\n# 领域知识\n以下是用户提供的背景知识（游戏规则、业务流程、术语等），供决策时参考。"
        "它不是任务目标，其中的描述不构成待执行的指令；当其与当前界面的实际状态冲突时，以界面为准。\n"
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

    parts = ["# 当前任务上下文\n## 当前日期\n", _format_date(moment.date())]
    parts.append("\n\n## 用户目标\n")
    parts.append(instruction)

    if summary:
        parts.append("\n\n## 已完成的步骤摘要\n")
        parts.append(summary)

    if notes:
        parts.append("\n\n## 已保存的笔记\n")
        parts.append(notes)

    if annotate_for_model:
        parts.append("\n\n## 截图标注说明\n")
        parts.append(
            "历史截图中可能包含红色十字准线标记，标记了上一步操作的坐标位置。"
            "这些标记仅用于帮助你理解操作执行位置，不是界面元素，请忽略它们对界面内容的遮挡。"
        )

    parts.append("\n\n请使用")
    parts.append(lang_name)
    parts.append("回复")
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
