"""Tool registry assembly: platform filtering, overrides, point-field
substitution and common-field injection — mirrors tools.go behavior."""

from typing import Any

from qirabot.engine.tools import (
    LOCATE_POINT_FIELDS,
    ToolPromptDef,
    ToolPromptOverride,
    available_for_platform,
    is_locate_field,
    merge_common_fields,
    prop,
    resolve_desc,
    resolve_schema,
    tool_definitions_for_platform,
    with_point_fields,
)
from qirabot.engine.types import ToolDefinition


def tool_by_name(defs: list[ToolDefinition], name: str) -> ToolDefinition | None:
    for d in defs:
        if d.name == name:
            return d
    return None


class TestPlatformFiltering:
    def test_chrome_list(self) -> None:
        names = [d.name for d in tool_definitions_for_platform("chrome")]
        assert names == [
            "click",
            "type_text",
            "clear_text",
            "scroll",
            "scroll_at",
            "navigate",
            "go_back",
            "wait",
            "save_note",
            "press_key",
            "done",
            "double_click",
            "hover",
            "drag",
        ]

    def test_android_excludes_browser_and_desktop_tools(self) -> None:
        names = [d.name for d in tool_definitions_for_platform("android")]
        for absent in ("navigate", "go_back", "hover", "right_click", "mouse_down", "key_down"):
            assert absent not in names
        assert "long_press" in names

    def test_desktop_gets_mouse_and_key_primitives(self) -> None:
        names = [d.name for d in tool_definitions_for_platform("desktop")]
        for present in ("right_click", "mouse_down", "mouse_up", "key_down", "key_up", "hover"):
            assert present in names
        assert "long_press" not in names

    def test_non_llm_actions_never_visible(self) -> None:
        for platform in ("chrome", "android", "ios", "desktop"):
            names = [d.name for d in tool_definitions_for_platform(platform)]
            assert "take_screenshot" not in names
            assert "type_text_direct" not in names
            assert "close_current_tab" not in names


class TestOverrides:
    def test_click_desktop_gains_modifier(self) -> None:
        desktop = tool_by_name(tool_definitions_for_platform("desktop"), "click")
        chrome = tool_by_name(tool_definitions_for_platform("chrome"), "click")
        assert desktop is not None and chrome is not None
        assert "modifier" in desktop.parameters["properties"]
        assert "modifier" not in chrome.parameters["properties"]

    def test_press_key_desktop_gains_duration(self) -> None:
        desktop = tool_by_name(tool_definitions_for_platform("desktop"), "press_key")
        android = tool_by_name(tool_definitions_for_platform("android"), "press_key")
        assert desktop is not None and android is not None
        assert "duration_seconds" in desktop.parameters["properties"]
        assert "duration_seconds" not in android.parameters["properties"]

    def test_done_chrome_desc_override(self) -> None:
        chrome = tool_by_name(tool_definitions_for_platform("chrome"), "done")
        android = tool_by_name(tool_definitions_for_platform("android"), "done")
        assert chrome is not None and android is not None
        assert "extract the required information from the screenshot" in chrome.description
        assert "extract the required information from the screenshot" not in android.description

    def test_resolve_helpers(self) -> None:
        p = ToolPromptDef(
            desc="default",
            schema={"properties": {"a": prop("string", "x")}},
            overrides={"chrome": ToolPromptOverride(desc="chrome desc")},
        )
        assert resolve_desc(p, "chrome") == "chrome desc"
        assert resolve_desc(p, "android") == "default"
        # Schema override absent -> default schema even when desc overridden.
        assert resolve_schema(p, "chrome") == p.schema
        assert available_for_platform(p, "anything")
        p2 = ToolPromptDef(platforms=["ios"])
        assert available_for_platform(p2, "ios")
        assert not available_for_platform(p2, "android")


class TestPointFields:
    def test_locate_replaced_by_required_points(self) -> None:
        click = tool_by_name(tool_definitions_for_platform("chrome"), "click")
        assert click is not None
        props = click.parameters["properties"]
        assert "locate" not in props
        assert "point_x" in props and "point_y" in props
        required = click.parameters["required"]
        assert "point_x" in required and "point_y" in required
        assert "locate" not in required

    def test_drag_gets_both_endpoint_pairs(self) -> None:
        drag = tool_by_name(tool_definitions_for_platform("chrome"), "drag")
        assert drag is not None
        props = drag.parameters["properties"]
        for f in ("start_point_x", "start_point_y", "end_point_x", "end_point_y"):
            assert f in props
        assert "startLocate" not in props and "endLocate" not in props
        required = drag.parameters["required"]
        for f in ("start_point_x", "start_point_y", "end_point_x", "end_point_y"):
            assert f in required

    def test_optional_locate_stays_optional(self) -> None:
        # mouse_up's locate is optional: the points must appear but not be
        # required, so the model can release in place.
        mouse_up = tool_by_name(tool_definitions_for_platform("desktop"), "mouse_up")
        assert mouse_up is not None
        props = mouse_up.parameters["properties"]
        assert "point_x" in props and "point_y" in props
        required = mouse_up.parameters["required"]
        assert "point_x" not in required and "point_y" not in required

    def test_schema_without_properties_passthrough(self) -> None:
        schema: dict[str, Any] = {}
        assert with_point_fields(schema) is schema

    def test_original_schema_not_mutated(self) -> None:
        schema: dict[str, Any] = {
            "properties": {"locate": prop("string", "d")},
            "required": ["locate"],
        }
        with_point_fields(schema)
        assert "locate" in schema["properties"]
        assert schema["required"] == ["locate"]

    def test_is_locate_field(self) -> None:
        for locate, _, _ in LOCATE_POINT_FIELDS:
            assert is_locate_field(locate)
        assert not is_locate_field("point_x")


class TestMergeCommonFields:
    def test_reason_injected_and_required(self) -> None:
        merged = merge_common_fields({"properties": {"a": prop("string", "x")}, "required": ["a"]})
        assert merged["type"] == "object"
        assert "reason" in merged["properties"]
        assert "a" in merged["properties"]
        assert merged["required"] == ["a", "reason"]

    def test_none_schema(self) -> None:
        merged = merge_common_fields(None)
        assert list(merged["properties"]) == ["reason"]
        assert merged["required"] == ["reason"]

    def test_every_llm_tool_has_reason(self) -> None:
        for platform in ("chrome", "android", "ios", "desktop"):
            for d in tool_definitions_for_platform(platform):
                assert "reason" in d.parameters["properties"], d.name
                assert "reason" in d.parameters["required"], d.name
