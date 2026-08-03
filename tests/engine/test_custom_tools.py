"""ParseCustomTools / ParseExcludeTools / knowledge validation — ported from
the Go server's custom_tools_test.go and knowledge_test.go."""

from typing import Any

import pytest

from qirabot.engine.custom_tools import (
    custom_tool_definitions,
    custom_tool_names,
    filter_excluded,
    parse_custom_tools,
    parse_exclude_tools,
    parse_knowledge,
)
from qirabot.engine.tools import tool_definitions_for_platform


def gm_tool() -> dict[str, Any]:
    return {
        "name": "gm_command",
        "description": "向GM后台发送指令",
        "parameters": {
            "properties": {
                "command": {"type": "string", "description": "指令"},
            },
            "required": ["command"],
        },
    }


class TestParseCustomTools:
    def test_valid_normalizes_required(self) -> None:
        defs = parse_custom_tools([gm_tool()])
        assert len(defs) == 1
        assert defs[0].name == "gm_command"
        assert defs[0].parameters is not None
        assert defs[0].parameters["required"] == ["command"]

    def test_no_parameters(self) -> None:
        defs = parse_custom_tools([{"name": "refresh_cache", "description": "刷新缓存"}])
        assert defs[0].parameters is None

    @pytest.mark.parametrize(
        ("raw", "want_err"),
        [
            ({}, "must be an array"),
            (["x"], "must be an object"),
            ([{**gm_tool(), "name": "GMCommand"}], "must match"),
            ([{**gm_tool(), "name": "1gm"}], "must match"),
            ([{**gm_tool(), "name": "click"}], "conflicts with a built-in"),
            ([{**gm_tool(), "name": "ai"}], "conflicts with a built-in"),
            ([{**gm_tool(), "name": "take_screenshot"}], "conflicts with a built-in"),
            ([gm_tool(), gm_tool()], "duplicate name"),
            ([{"name": "gm_command"}], "description is required"),
            ([{**gm_tool(), "description": "a" * 1025}], "exceeds 1024"),
            ([{**gm_tool(), "parameters": "x"}], "must be an object"),
            (
                [{**gm_tool(), "parameters": {"properties": {"reason": {"type": "string"}}}}],
                "reserved",
            ),
            (
                [{**gm_tool(), "parameters": {"properties": {"locate": {"type": "string"}}}}],
                "reserved",
            ),
            (
                [{**gm_tool(), "parameters": {"properties": {"x": {"type": "integer"}}}}],
                "reserved",
            ),
            (
                [
                    {
                        **gm_tool(),
                        "parameters": {
                            "properties": {"a": {"type": "string"}},
                            "required": ["b"],
                        },
                    }
                ],
                "not declared in properties",
            ),
            (
                [
                    {
                        **gm_tool(),
                        "parameters": {
                            "properties": {"a": {"type": "string"}},
                            "required": [1],
                        },
                    }
                ],
                "array of strings",
            ),
        ],
    )
    def test_errors(self, raw: Any, want_err: str) -> None:
        with pytest.raises(ValueError) as ei:
            parse_custom_tools(raw)
        assert want_err in str(ei.value)

    def test_too_many(self) -> None:
        items = []
        for i in range(17):
            m = gm_tool()
            m["name"] = "tool_" + chr(ord("a") + i)
            items.append(m)
        with pytest.raises(ValueError) as ei:
            parse_custom_tools(items)
        assert "at most 16" in str(ei.value)

    def test_total_size_limit(self) -> None:
        items = []
        for i in range(16):
            m = gm_tool()
            m["name"] = "tool_" + chr(ord("a") + i)
            m["description"] = "a" * 1024
            items.append(m)
        with pytest.raises(ValueError) as ei:
            parse_custom_tools(items)
        assert "bytes total" in str(ei.value)

    def test_empty_list(self) -> None:
        assert parse_custom_tools([]) == []


class TestParseExcludeTools:
    @pytest.mark.parametrize(
        ("raw", "platform", "want"),
        [
            (["scroll", "hover"], "chrome", ["scroll", "hover"]),
            (["scroll", "scroll", "hover"], "chrome", ["scroll", "hover"]),
            (["navigate"], "chrome", ["navigate"]),
        ],
    )
    def test_valid(self, raw: Any, platform: str, want: list[str]) -> None:
        assert parse_exclude_tools(raw, platform) == want

    @pytest.mark.parametrize(
        ("raw", "platform", "want_err"),
        [
            ("scroll", "chrome", "must be an array"),
            ([1], "chrome", "must be a string"),
            (["done"], "chrome", "cannot be excluded"),
            (["scrol"], "chrome", "unknown or unavailable"),
            (["take_screenshot"], "chrome", "unknown or unavailable"),
            (["navigate"], "android", "unknown or unavailable"),
        ],
    )
    def test_errors(self, raw: Any, platform: str, want_err: str) -> None:
        with pytest.raises(ValueError) as ei:
            parse_exclude_tools(raw, platform)
        assert want_err in str(ei.value)


class TestParseKnowledge:
    def test_valid(self) -> None:
        assert parse_knowledge("GM 只能使用一次") == "GM 只能使用一次"

    def test_empty_allowed(self) -> None:
        assert parse_knowledge("") == ""

    @pytest.mark.parametrize("raw", [42.0, True, ["a"], {"k": "v"}])
    def test_non_string_rejected(self, raw: Any) -> None:
        with pytest.raises(ValueError):
            parse_knowledge(raw)

    def test_over_limit_rejected(self) -> None:
        with pytest.raises(ValueError) as ei:
            parse_knowledge("x" * (32 * 1024 + 1))
        assert "exceeds" in str(ei.value)

    def test_limit_counts_utf8_bytes(self) -> None:
        # 11000 CJK chars ≈ 33KB UTF-8: over the byte limit even though the
        # character count is far below it.
        with pytest.raises(ValueError):
            parse_knowledge("知" * 11000)


class TestDefinitions:
    def test_reason_injected_and_required(self) -> None:
        defs = parse_custom_tools([gm_tool(), {"name": "no_args", "description": "d"}])
        tools = custom_tool_definitions(defs)
        assert len(tools) == 2
        for tool in tools:
            props = tool.parameters["properties"]
            assert "reason" in props
            assert "reason" in tool.parameters["required"]
        gm = next(t for t in tools if t.name == "gm_command")
        assert "command" in gm.parameters["properties"]

    def test_no_point_fields_injected(self) -> None:
        # Custom tools never go through the locate/point-field transform.
        defs = parse_custom_tools([gm_tool()])
        tools = custom_tool_definitions(defs)
        props = tools[0].parameters["properties"]
        assert "point_x" not in props and "point_y" not in props

    def test_client_order_preserved(self) -> None:
        defs = parse_custom_tools(
            [{"name": "tool_b", "description": "b"}, {"name": "tool_a", "description": "a"}]
        )
        tools = custom_tool_definitions(defs)
        assert [t.name for t in tools] == ["tool_b", "tool_a"]

    def test_names(self) -> None:
        defs = parse_custom_tools([gm_tool()])
        assert custom_tool_names(defs) == {"gm_command"}
        assert custom_tool_names([]) == set()


class TestFilterExcluded:
    def test_removes_named_tools(self) -> None:
        tools = tool_definitions_for_platform("chrome")
        filtered = filter_excluded(tools, ["scroll", "hover"])
        names = [t.name for t in filtered]
        assert "scroll" not in names and "hover" not in names
        assert "done" in names

    def test_empty_exclude_returns_same(self) -> None:
        tools = tool_definitions_for_platform("chrome")
        assert filter_excluded(tools, []) is tools
