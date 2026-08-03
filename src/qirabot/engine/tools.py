"""Built-in tool registry — the single source of truth for action metadata.

Mirrors internal/decision/tools.go. Registry order determines the LLM tool
list order (and therefore the prompt-cache prefix), so it must stay stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import actions
from .types import ToolDefinition


@dataclass
class ToolPromptOverride:
    """Overrides description or schema for a specific platform."""

    desc: str = ""  # empty = use default
    schema: dict[str, Any] | None = None  # None = use default


@dataclass
class ToolPromptDef:
    """How a tool appears to the LLM via Function Calling."""

    desc: str = ""
    # JSON Schema (tool-specific, without common fields)
    schema: dict[str, Any] = field(default_factory=dict)
    platforms: list[str] = field(default_factory=list)  # empty = all
    overrides: dict[str, ToolPromptOverride] = field(default_factory=dict)


@dataclass
class ToolDef:
    """A single tool/action in the system."""

    type: str
    prompt: ToolPromptDef | None = None  # None = not visible to LLM


def prop(typ: str, desc: str) -> dict[str, Any]:
    """Build a JSON Schema property definition."""
    return {"type": typ, "description": desc}


def prop_enum(typ: str, desc: str, *values: str) -> dict[str, Any]:
    """Build a JSON Schema property with enum constraint."""
    return {"type": typ, "description": desc, "enum": list(values)}


TOOL_REGISTRY: list[ToolDef] = [
    # ---- LLM-visible tools ----
    ToolDef(
        type=actions.CLICK,
        prompt=ToolPromptDef(
            desc=(
                "Click an interactive element such as a button, link or option. Do not use it "
                "to focus an input field; use type_text to enter text in one step"
            ),
            schema={
                "properties": {
                    "locate": prop("string", "A uniquely identifying description of the element"),
                },
                "required": ["locate"],
            },
            overrides={
                actions.PLATFORM_DESKTOP: ToolPromptOverride(
                    schema={
                        "properties": {
                            "locate": prop("string", "A uniquely identifying description of the element"),
                            "modifier": prop(
                                "string",
                                "Modifier key held while clicking (alt|ctrl|shift|win); join "
                                "multiple with + (e.g. ctrl+shift). Use only when a modifier "
                                "click is explicitly needed (e.g. alt+click in games, "
                                "ctrl+click for multi-select); leave empty for normal clicks",
                            ),
                        },
                        "required": ["locate"],
                    },
                ),
            },
        ),
    ),
    ToolDef(
        type=actions.TYPE_TEXT,
        prompt=ToolPromptDef(
            desc="Automatically focus the input field and type text, no prior click needed",
            schema={
                "properties": {
                    "locate": prop("string", "A uniquely identifying description of the input field"),
                    "text": prop("string", "The text to type"),
                    "press_enter": prop("boolean", "Press Enter after typing, default false"),
                    "clear_before_typing": prop(
                        "boolean", "Clear existing content before typing, default false"
                    ),
                },
                "required": ["locate", "text"],
            },
        ),
    ),
    ToolDef(
        type=actions.CLEAR_TEXT,
        prompt=ToolPromptDef(
            desc=(
                "Automatically focus the input field and clear all of its content, "
                "no prior click needed"
            ),
            schema={
                "properties": {
                    "locate": prop("string", "A uniquely identifying description of the input field"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.SCROLL,
        prompt=ToolPromptDef(
            desc="Scroll the whole page. Use when the target element is outside the visible area",
            schema={
                "properties": {
                    "direction": prop_enum("string", "Scroll direction", "up", "down", "left", "right"),
                    "type": prop_enum(
                        "string",
                        "Scroll type; until_* keeps scrolling until the boundary is reached",
                        "once",
                        "until_bottom",
                        "until_top",
                        "until_left",
                        "until_right",
                    ),
                    "amount": prop(
                        "integer",
                        "Scroll distance in pixels; must be set appropriately for the context: "
                        "dropdown menus/small lists 50~100, normal lists 200~400, "
                        "long pages 500~1000",
                    ),
                },
                "required": ["direction", "amount"],
            },
        ),
    ),
    ToolDef(
        type=actions.SCROLL_AT,
        prompt=ToolPromptDef(
            desc=(
                "Scroll inside a specific scrollable container (e.g. a sidebar or dialog list) "
                "without affecting other areas of the page"
            ),
            schema={
                "properties": {
                    "locate": prop("string", "Description of the scrollable area"),
                    "direction": prop_enum("string", "Scroll direction", "up", "down", "left", "right"),
                    "amount": prop(
                        "integer",
                        "Scroll distance in pixels; defaults to 80% of the area's height. "
                        "Choose a distance appropriate to the container's height",
                    ),
                },
                "required": ["locate", "direction"],
            },
        ),
    ),
    ToolDef(
        type=actions.NAVIGATE,
        prompt=ToolPromptDef(
            desc=(
                "Open a URL. Preferred for the first step, or when the page does not match "
                "the task or is blank"
            ),
            platforms=[actions.PLATFORM_CHROME],
            schema={
                "properties": {
                    "url": prop("string", "The URL to open"),
                },
                "required": ["url"],
            },
        ),
    ),
    ToolDef(
        type=actions.GO_BACK,
        prompt=ToolPromptDef(
            desc="Go back to the previous page or close the current tab",
            platforms=[actions.PLATFORM_CHROME],
            schema={},
        ),
    ),
    ToolDef(type=actions.CLOSE_TAB),
    ToolDef(
        type=actions.WAIT,
        prompt=ToolPromptDef(
            desc=(
                "Wait for the page to load, an animation to finish or an async operation to "
                "return. Use when you see loading indicators, spinners or skeleton screens"
            ),
            schema={
                "properties": {
                    "duration": prop("integer", "Milliseconds to wait"),
                },
                "required": ["duration"],
            },
        ),
    ),
    ToolDef(
        type=actions.SAVE_NOTE,
        prompt=ToolPromptDef(
            desc=(
                "Save intermediate information for later steps. Use when: collecting across "
                "pages/screens, recording key data, or the content spans multiple screens of "
                "scrolling or paging and must be collected screen by screen. Only when the "
                "target content is fully visible on the current screen may you skip saving and "
                "call done directly. Keep the original text uncompressed; do not save "
                "duplicates."
            ),
            schema={
                "properties": {
                    "content": prop("string", "The information to save"),
                },
                "required": ["content"],
            },
        ),
    ),
    ToolDef(
        type=actions.PRESS_KEY,
        prompt=ToolPromptDef(
            desc="Simulate a key press or key combination",
            schema={
                "properties": {
                    "key": prop("string", "Key name (Enter|Back|Home)"),
                },
                "required": ["key"],
            },
            overrides={
                actions.PLATFORM_CHROME: ToolPromptOverride(
                    schema={
                        "properties": {
                            "key": prop(
                                "string",
                                "A single key (Enter|Backspace|Tab|Escape|ArrowDown|PageUp|"
                                "PageDown, etc.) or a combination joined with + (ctrl+c|ctrl+w|"
                                "ctrl+t, etc.). Prefer go_back for navigating back. "
                                "PageUp/PageDown can be used for full-screen scrolling",
                            ),
                        },
                        "required": ["key"],
                    },
                ),
                actions.PLATFORM_DESKTOP: ToolPromptOverride(
                    schema={
                        "properties": {
                            "key": prop(
                                "string",
                                "A single key (Enter|Backspace|Tab|Escape, etc.) or a "
                                "combination joined with + (ctrl+c|alt+tab, etc.)",
                            ),
                            "duration_seconds": prop(
                                "number",
                                "How long to hold the key, in seconds; omit for an instant tap. "
                                "Use for scenarios that need a held key, such as in-game "
                                "movement: 0.1~0.5 for light taps/fine adjustment, 1~3 for "
                                "sustained movement, max 10. An instant tap may be missed by a "
                                "game for being too short; use at least 0.1 in games",
                            ),
                        },
                        "required": ["key"],
                    },
                ),
            },
        ),
    ),
    ToolDef(
        type=actions.DONE,
        prompt=ToolPromptDef(
            desc=(
                "Call when the task ends. Parameters:\n"
                "- success: true = the task goal has been achieved; false = blocked and unable "
                "to continue\n"
                "- result: when success=true, the complete final result (consolidate saved "
                "notes if any, otherwise extract directly from the current screen); when "
                "success=false, the reason you cannot continue (e.g. login required, captcha, "
                "insufficient permissions, repeated actions had no effect)"
            ),
            schema={
                "properties": {
                    "success": prop("boolean", "Whether the task completed successfully"),
                    "result": prop(
                        "string",
                        "On success, the complete final result (consolidating all saved "
                        "notes); on failure, the reason you cannot continue",
                    ),
                },
                "required": ["success", "result"],
            },
            overrides={
                actions.PLATFORM_CHROME: ToolPromptOverride(
                    desc=(
                        "Call when the task ends. Parameters:\n"
                        "- success: true = the task goal has been achieved; false = blocked "
                        "and unable to continue\n"
                        "- result: when success=true, the complete final result (extract the "
                        "required information from the screenshot, consolidating saved notes "
                        "if any); when success=false, the reason you cannot continue (e.g. "
                        "login required, captcha, insufficient permissions, repeated actions "
                        "had no effect)"
                    ),
                ),
            },
        ),
    ),
    ToolDef(
        type=actions.DOUBLE_CLICK,
        prompt=ToolPromptDef(
            desc="Double-click the target element (e.g. to select text or expand a collapsed item)",
            schema={
                "properties": {
                    "locate": prop("string", "A uniquely identifying description of the target element"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.RIGHT_CLICK,
        prompt=ToolPromptDef(
            desc="Right-click the target element",
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "locate": prop("string", "A uniquely identifying description of the target element"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.HOVER,
        prompt=ToolPromptDef(
            desc=(
                "Hover over the target element to trigger tooltips, dropdown menus and other "
                "hover effects"
            ),
            platforms=[actions.PLATFORM_DESKTOP, actions.PLATFORM_CHROME],
            schema={
                "properties": {
                    "locate": prop("string", "A uniquely identifying description of the element"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.DRAG,
        prompt=ToolPromptDef(
            desc="Drag from a start point to an end point",
            schema={
                "properties": {
                    "startLocate": prop(
                        "string", "A uniquely identifying description of the drag start element"
                    ),
                    "endLocate": prop(
                        "string", "A uniquely identifying description of the drag end element"
                    ),
                },
                "required": ["startLocate", "endLocate"],
            },
        ),
    ),
    ToolDef(
        type=actions.LONG_PRESS,
        prompt=ToolPromptDef(
            desc=(
                "Long-press the target element (to trigger a context menu, enter edit/"
                "selection mode, prepare a drag, etc.). Touch screens only"
            ),
            platforms=[actions.PLATFORM_ANDROID, actions.PLATFORM_IOS],
            schema={
                "properties": {
                    "locate": prop("string", "A uniquely identifying description of the target element"),
                    "duration": prop("integer", "Long-press duration in milliseconds, default 2000"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.MOUSE_DOWN,
        prompt=ToolPromptDef(
            desc=(
                "Press and hold the left mouse button (start of a drag, sustained aiming/"
                "charging, etc.). Must be released with mouse_up in a later step, otherwise "
                "every subsequent click becomes a drag. Desktop only"
            ),
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "locate": prop(
                        "string",
                        "A uniquely identifying description of the element to press the mouse on",
                    ),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.MOUSE_UP,
        prompt=ToolPromptDef(
            desc=(
                "Release the left mouse button, paired with mouse_down. locate is optional: "
                "if given, move to that element before releasing (drag to the target); if "
                "omitted, release at the current position. Desktop only"
            ),
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "locate": prop(
                        "string",
                        "Description of the element at the release position; omit to release "
                        "at the current cursor position",
                    ),
                },
            },
        ),
    ),
    ToolDef(
        type=actions.KEY_DOWN,
        prompt=ToolPromptDef(
            desc=(
                "Press and hold a key (hold w for sustained movement in a game, hold shift, "
                "etc.). Must be released with key_up in a later step, otherwise the key stays "
                "active. Desktop only"
            ),
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "key": prop(
                        "string", "Name of the key to hold (e.g. w / space / shift / ctrl / Enter)"
                    ),
                },
                "required": ["key"],
            },
        ),
    ),
    ToolDef(
        type=actions.KEY_UP,
        prompt=ToolPromptDef(
            desc="Release a key previously held with key_down. Desktop only",
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "key": prop("string", "Name of the key to release; must match key_down"),
                },
                "required": ["key"],
            },
        ),
    ),
    # ---- Non-LLM actions (prompt = None) ----
    ToolDef(type=actions.TYPE_TEXT_DIRECT),
    ToolDef(type=actions.SCREENSHOT),
]


# Pairs each locate-style field with the two scalar coordinate fields the
# model fills when grounding inline. An ordered list (not a dict lookup) so
# the generated schema — particularly the `required` list — is deterministic
# across calls. Scalars (not an [x,y] array) because Gemini's schema handling
# drops array `items`.
LOCATE_POINT_FIELDS: list[tuple[str, str, str]] = [
    ("locate", "point_x", "point_y"),
    ("startLocate", "start_point_x", "start_point_y"),
    ("endLocate", "end_point_x", "end_point_y"),
]


def is_locate_field(name: str) -> bool:
    return any(f[0] == name for f in LOCATE_POINT_FIELDS)


def tool_definitions_for_platform(platform: str) -> list[ToolDefinition]:
    """LLM tool definitions for a platform. The common meta-field (reason) is
    injected into every schema, and each locate field is replaced by scalar
    point fields so the model emits coordinates directly."""
    defs: list[ToolDefinition] = []
    for tool in TOOL_REGISTRY:
        if tool.prompt is None:
            continue
        if not available_for_platform(tool.prompt, platform):
            continue
        schema = with_point_fields(resolve_schema(tool.prompt, platform))
        defs.append(
            ToolDefinition(
                name=tool.type,
                description=resolve_desc(tool.prompt, platform),
                parameters=merge_common_fields(schema),
            )
        )
    return defs


def with_point_fields(schema: dict[str, Any]) -> dict[str, Any]:
    """Copy of schema with each locate field replaced by scalar point_x/point_y
    fields (required iff the locate field was)."""
    props = schema.get("properties")
    if not isinstance(props, dict) or not props:
        return schema

    new_props: dict[str, Any] = dict(props)
    required = schema.get("required")
    required_list: list[str] = list(required) if isinstance(required, list) else []
    required_set = set(required_list)
    new_required = list(required_list)

    for locate, fx, fy in LOCATE_POINT_FIELDS:
        if locate not in props:
            continue
        # Coordinate convention is stated once in the system prompt; keep these
        # per-field descriptions minimal so the schema doesn't repeat it.
        new_props[fx] = prop("integer", "x coordinate")
        new_props[fy] = prop("integer", "y coordinate")
        if locate in required_set:
            new_required.extend([fx, fy])
        del new_props[locate]

    new_required = [r for r in new_required if not is_locate_field(r)]

    out = dict(schema)
    out["properties"] = new_props
    out["required"] = new_required
    return out


def available_for_platform(p: ToolPromptDef, platform: str) -> bool:
    if not p.platforms:
        return True
    return platform in p.platforms


def resolve_desc(p: ToolPromptDef, platform: str) -> str:
    ov = p.overrides.get(platform)
    if ov is not None and ov.desc:
        return ov.desc
    return p.desc


def resolve_schema(p: ToolPromptDef, platform: str) -> dict[str, Any]:
    ov = p.overrides.get(platform)
    if ov is not None and ov.schema is not None:
        return ov.schema
    return p.schema


def merge_common_fields(schema: dict[str, Any] | None) -> dict[str, Any]:
    """Inject the common `reason` field into a tool schema."""
    props: dict[str, Any] = {
        "reason": prop(
            "string",
            "Describe what changed in the UI and give the detailed reasoning for this decision",
        ),
    }
    if schema:
        sp = schema.get("properties")
        if isinstance(sp, dict):
            props.update(sp)

    required: list[str] = []
    if schema:
        sr = schema.get("required")
        if isinstance(sr, list):
            required.extend(s for s in sr if isinstance(s, str))
    required.append("reason")

    return {"type": "object", "properties": props, "required": required}
