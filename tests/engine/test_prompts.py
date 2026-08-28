"""System-prompt assembly and conversation replay — ported from the Go
server's prompts.go behavior plus knowledge_test.go's prompt-placement tests."""

from datetime import datetime

from qirabot.engine.prompts import (
    LOCATE_SYSTEM_PROMPT,
    NORMALIZED_COORD_RULE,
    PLATFORM_PROMPTS,
    build_conversation_messages,
    build_dynamic_prompt,
    build_system_prompt,
    excluded_tools_section,
    format_notes,
    parse_history_args,
    resolve_prompt,
)
from qirabot.engine.custom_tools import parse_custom_tools
from qirabot.engine.types import ConversationTurn, DecisionInput, detect_image_mime

NOW = datetime(2026, 8, 2, 12, 0, 0)  # a Sunday

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 8


class TestPlatformPrompts:
    def test_all_platforms_loaded(self) -> None:
        assert sorted(PLATFORM_PROMPTS) == ["android", "chrome", "desktop", "ios"]
        for text in PLATFORM_PROMPTS.values():
            assert text.startswith("# Role")

    def test_unknown_platform_falls_back_to_android(self) -> None:
        assert resolve_prompt(PLATFORM_PROMPTS, "playstation") == PLATFORM_PROMPTS["android"]


class TestSystemPrompts:
    def test_knowledge_ahead_of_task_context(self) -> None:
        # Knowledge is session-constant, so it must sit in the task-stable
        # front of the prompt, ahead of the instruction-bearing tail — the
        # section order is what keeps the prompt-cache prefix long.
        p = build_system_prompt(
            "android", "完成每日副本", "GM 命令整个任务只能使用一次", "zh", False, [], NOW
        )
        assert "GM 命令整个任务只能使用一次" in p
        assert p.index("# Domain knowledge") < p.index("# Current task context")
        # Framing: reference material, not goal.
        assert "not the task goal" in p

    def test_no_knowledge_no_section(self) -> None:
        p = build_system_prompt("android", "完成每日副本", "", "zh", False, [], NOW)
        assert "# Domain knowledge" not in p

    def test_grounding_guidance_always_present(self) -> None:
        p = build_system_prompt("chrome", "goal", "", "zh", False, [], NOW)
        assert "# Coordinate output" in p
        assert NORMALIZED_COORD_RULE in p
        assert "start_point_x" in p

    def test_excluded_tools_ahead_of_task_context(self) -> None:
        p = build_system_prompt("chrome", "goal", "", "zh", False, ["scroll", "hover"], NOW)
        assert "`scroll`, `hover`" in p
        assert p.index("# Tool availability") < p.index("# Current task context")

    def test_excluded_tools_section_empty(self) -> None:
        assert excluded_tools_section([]) == ""


class TestDynamicPrompt:
    def test_date_goal_language(self) -> None:
        p = build_dynamic_prompt("买一杯咖啡", "zh-CN", False, NOW)
        assert "## Current date\n2026-08-02 Sunday" in p
        assert "## User goal\n买一杯咖啡" in p
        assert p.endswith("Respond in 中文")
        assert "## Screenshot annotations" not in p

    def test_annotate_section(self) -> None:
        p = build_dynamic_prompt("goal", "en", True, NOW)
        assert "## Screenshot annotations" in p
        assert "red crosshair" in p
        assert p.endswith("Respond in English")

    def test_system_prompt_constant_within_task(self) -> None:
        # Cache-stability invariant: nothing that changes step to step
        # (summary, notes) may appear in the system prompt — a change at the
        # head of the token stream invalidates the whole provider cache
        # prefix. Summary/notes travel via progress_context_section.
        p = build_system_prompt("chrome", "goal", "知识", "en", True, [], NOW)
        assert "## Summary of completed steps" not in p
        assert "## Saved notes" not in p

    def test_no_language_follows_instruction(self) -> None:
        p = build_dynamic_prompt("买一杯咖啡", "", False, NOW)
        assert p.endswith("Respond in the same language as the user's instruction")


class TestConversationMessages:
    def test_first_step_shape(self) -> None:
        msgs = build_conversation_messages(
            DecisionInput(instruction="do it", is_first_step=True, current_screenshot=PNG)
        )
        assert len(msgs) == 2
        assert msgs[0].role == "user"
        assert msgs[0].content == "Task: do it\n\nPlease begin."
        assert msgs[1].images[0].mime_type == "image/png"

    def test_continue_wording_after_first_step(self) -> None:
        msgs = build_conversation_messages(
            DecisionInput(instruction="do it", is_first_step=False, current_screenshot=PNG)
        )
        assert msgs[0].content == "Task: do it\n\nContinue from where you left off."

    def test_first_step_without_screenshot_gets_placeholder(self) -> None:
        msgs = build_conversation_messages(DecisionInput(instruction="do it", is_first_step=True))
        assert msgs[-1].content == (
            "(no screenshot available, decide the first action based on the task description)"
        )

    def test_no_placeholder_after_first_step(self) -> None:
        msgs = build_conversation_messages(DecisionInput(instruction="do it", is_first_step=False))
        assert len(msgs) == 1  # only the task message

    def test_history_replayed_as_tool_triads(self) -> None:
        input = DecisionInput(
            instruction="do it",
            current_screenshot=PNG,
            history=[
                ConversationTurn(
                    screenshot_data=JPEG,
                    action_type="click",
                    action_params='{"point_x":500,"point_y":300}',
                    reasoning="点击登录",
                )
            ],
        )
        msgs = build_conversation_messages(input)
        # task, history screenshot, assistant tool_call, tool result, current screenshot
        assert [m.role for m in msgs] == ["user", "user", "assistant", "tool", "user"]
        assert msgs[1].images[0].mime_type == "image/jpeg"
        # History screenshots are context only ("did the UI change?") — always
        # low resolution. The current screenshot follows the task-level setting.
        assert msgs[1].images[0].resolution == "low"
        assert msgs[4].images[0].resolution == ""
        call = msgs[2].tool_calls[0]
        assert call.id == "click" and call.name == "click"
        # History replays the model's own normalized frame plus the reason.
        assert call.args == {"point_x": 500, "point_y": 300, "reason": "点击登录"}
        result = msgs[3].tool_results[0]
        assert result.tool_call_id == "click"
        # Default output "ok" + the screenshot-verify nudge.
        assert result.content == "ok\nVerify the actual result from the screenshot"

    def test_custom_turn_skips_verify_suffix(self) -> None:
        defs = parse_custom_tools(
            [
                {
                    "name": "gm_command",
                    "description": "向GM后台发送指令",
                    "parameters": {
                        "properties": {"command": {"type": "string", "description": "指令"}},
                        "required": ["command"],
                    },
                }
            ]
        )
        input = DecisionInput(
            instruction="do it",
            custom_tools=defs,
            history=[
                ConversationTurn(
                    action_type="gm_command",
                    action_params='{"command":"add_energy 100"}',
                    tool_output="OK: energy +100",
                ),
                ConversationTurn(action_type="click", action_params='{"locate":"开始按钮"}'),
            ],
        )
        msgs = build_conversation_messages(input)
        results = {
            tr.tool_call_id: tr.content for m in msgs for tr in m.tool_results
        }
        assert results["gm_command"] == "OK: energy +100"
        assert "Verify the actual result from the screenshot" in results["click"]
        assert results["click"].startswith("ok")

    def test_correction_hint_goes_last(self) -> None:
        msgs = build_conversation_messages(
            DecisionInput(
                instruction="do it",
                current_screenshot=PNG,
                correction_hint="坐标越界，请重新输出",
            )
        )
        assert msgs[-1].content == "坐标越界，请重新输出"
        assert msgs[-2].images  # current screenshot right before the hint

    def test_progress_context_between_history_and_screenshot(self) -> None:
        # Summary/notes change during the task, so they must sit near the
        # TAIL — after the stable history triads, before the current
        # screenshot — never in the system prompt where a change would
        # invalidate the whole cache prefix.
        input = DecisionInput(
            instruction="do it",
            current_screenshot=PNG,
            summary="click: 打开设置",
            notes=["第一页内容", "第二页内容"],
            history=[ConversationTurn(action_type="click", action_params="{}")],
            correction_hint="重新输出",
        )
        msgs = build_conversation_messages(input)
        # task, tool_call, tool result, progress context, screenshot, hint
        assert [m.role for m in msgs] == ["user", "assistant", "tool", "user", "user", "user"]
        progress = msgs[3].content
        assert progress.startswith("# Progress context")
        assert "## Summary of completed steps\nclick: 打开设置" in progress
        assert "## Saved notes\n第一页内容\n---\n第二页内容" in progress
        assert msgs[4].images  # current screenshot after progress context

    def test_no_progress_context_when_empty(self) -> None:
        msgs = build_conversation_messages(
            DecisionInput(instruction="do it", current_screenshot=PNG)
        )
        assert not any(
            m.content.startswith("# Progress context") for m in msgs if m.content
        )


class TestHelpers:
    def test_parse_history_args(self) -> None:
        assert parse_history_args("") == {}
        assert parse_history_args("not json") == {}
        assert parse_history_args("[1,2]") == {}
        assert parse_history_args('{"a":1}') == {"a": 1}

    def test_format_notes(self) -> None:
        assert format_notes([]) == ""
        assert format_notes(["a"]) == "a"
        assert format_notes(["a", "b"]) == "a\n---\nb"

    def test_detect_image_mime(self) -> None:
        assert detect_image_mime(PNG) == "image/png"
        assert detect_image_mime(JPEG) == "image/jpeg"
        assert detect_image_mime(b"GIF89a" + b"\x00" * 8) == "image/gif"
        assert detect_image_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
        assert detect_image_mime(b"unknown") == "image/png"

    def test_locate_prompt_mentions_report_location(self) -> None:
        assert "report_location" in LOCATE_SYSTEM_PROMPT
        assert "found=false" in LOCATE_SYSTEM_PROMPT
