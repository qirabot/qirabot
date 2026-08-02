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
            desc="单击按钮、链接、选项等可交互元素。不要用于聚焦输入框, 输入文本用type_text一步到位",
            schema={
                "properties": {
                    "locate": prop("string", "元素的唯一性描述"),
                },
                "required": ["locate"],
            },
            overrides={
                actions.PLATFORM_DESKTOP: ToolPromptOverride(
                    schema={
                        "properties": {
                            "locate": prop("string", "元素的唯一性描述"),
                            "modifier": prop(
                                "string",
                                "点击时按住的修饰键(alt|ctrl|shift|win)，多个用+连接(如ctrl+shift)。"
                                "仅在明确需要修饰键点击时使用(如游戏中alt+点击、多选ctrl+点击)，普通点击不要填",
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
            desc="自动聚焦输入框、输入文本，无需先点击",
            schema={
                "properties": {
                    "locate": prop("string", "输入框的唯一性描述"),
                    "text": prop("string", "要输入的文本"),
                    "press_enter": prop("boolean", "输入后按回车，默认false"),
                    "clear_before_typing": prop("boolean", "输入前清空已有内容，默认false"),
                },
                "required": ["locate", "text"],
            },
        ),
    ),
    ToolDef(
        type=actions.CLEAR_TEXT,
        prompt=ToolPromptDef(
            desc="自动聚焦并清空输入框中所有内容，无需先点击",
            schema={
                "properties": {
                    "locate": prop("string", "输入框的唯一性描述"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.SCROLL,
        prompt=ToolPromptDef(
            desc="滚动整个页面。目标元素不在可视区域时使用",
            schema={
                "properties": {
                    "direction": prop_enum("string", "滚动方向", "up", "down", "left", "right"),
                    "type": prop_enum(
                        "string",
                        "滚动类型，until_*会持续滚动到边界",
                        "once",
                        "until_bottom",
                        "until_top",
                        "until_left",
                        "until_right",
                    ),
                    "amount": prop(
                        "integer",
                        "滚动像素值，必须根据场景设置合适的值：下拉菜单/小列表50~100，普通列表200~400，长页面500~1000",
                    ),
                },
                "required": ["direction", "amount"],
            },
        ),
    ),
    ToolDef(
        type=actions.SCROLL_AT,
        prompt=ToolPromptDef(
            desc="在指定可滚动容器内滚动（如侧边栏、弹窗列表），不影响页面其他区域",
            schema={
                "properties": {
                    "locate": prop("string", "滚动区域描述"),
                    "direction": prop_enum("string", "滚动方向", "up", "down", "left", "right"),
                    "amount": prop(
                        "integer",
                        "滚动像素值，不填则滚动区域80%高度。按照容器高度选择合适的滚动距离",
                    ),
                },
                "required": ["locate", "direction"],
            },
        ),
    ),
    ToolDef(
        type=actions.NAVIGATE,
        prompt=ToolPromptDef(
            desc="打开URL，首步、页面不匹配或空白时首选",
            platforms=[actions.PLATFORM_CHROME],
            schema={
                "properties": {
                    "url": prop("string", "要打开的URL"),
                },
                "required": ["url"],
            },
        ),
    ),
    ToolDef(
        type=actions.GO_BACK,
        prompt=ToolPromptDef(
            desc="返回上一页或关闭当前tab",
            platforms=[actions.PLATFORM_CHROME],
            schema={},
        ),
    ),
    ToolDef(type=actions.CLOSE_TAB),
    ToolDef(
        type=actions.WAIT,
        prompt=ToolPromptDef(
            desc="等待页面加载、动画完成或异步操作返回结果。看到加载中、转圈、骨架屏时使用",
            schema={
                "properties": {
                    "duration": prop("integer", "等待毫秒数"),
                },
                "required": ["duration"],
            },
        ),
    ),
    ToolDef(
        type=actions.SAVE_NOTE,
        prompt=ToolPromptDef(
            desc=(
                "保存中间信息供后续步骤使用。适用场景：跨页面/多界面收集、记录关键数据、"
                "内容需多屏滚动或翻页时分屏收集。仅当目标内容在当前一屏内全部可见时无需save直接done。"
                "保持原文不压缩，勿重复保存。"
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
            desc="模拟按键或组合键",
            schema={
                "properties": {
                    "key": prop("string", "按键名称 (Enter|Back|Home)"),
                },
                "required": ["key"],
            },
            overrides={
                actions.PLATFORM_CHROME: ToolPromptOverride(
                    schema={
                        "properties": {
                            "key": prop(
                                "string",
                                "单键(Enter|Backspace|Tab|Escape|ArrowDown|PageUp|PageDown等)"
                                "或组合键用+连接(ctrl+c|ctrl+w|ctrl+t等)，后退优先使用go_back。"
                                "PageUp/PageDown可用于整屏滚动",
                            ),
                        },
                        "required": ["key"],
                    },
                ),
                actions.PLATFORM_DESKTOP: ToolPromptOverride(
                    schema={
                        "properties": {
                            "key": prop(
                                "string", "单键(Enter|Backspace|Tab|Escape等)或组合键用+连接(ctrl+c|alt+tab等)"
                            ),
                            "duration_seconds": prop(
                                "number",
                                "按住时长(秒)，不填为瞬时点按。游戏内移动等需要持续按住的场景使用："
                                "0.1~0.5轻点微调，1~3持续移动，上限10。瞬时点按在游戏中可能因过短被漏采，游戏内建议至少0.1",
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
                "任务结束时调用。参数说明：\n"
                "- success: true=任务目标已完成，false=遇到阻碍无法继续\n"
                "- result: success=true时填写完整最终结果（如有已保存笔记则一并整合，"
                "无笔记则直接从当前屏幕提取），success=false时填写无法继续的原因"
                "（如需要登录、验证码、权限不足、反复操作无效）"
            ),
            schema={
                "properties": {
                    "success": prop("boolean", "任务是否成功完成"),
                    "result": prop("string", "成功时为完整最终结果（整合所有已保存笔记），失败时为无法继续的原因"),
                },
                "required": ["success", "result"],
            },
            overrides={
                actions.PLATFORM_CHROME: ToolPromptOverride(
                    desc=(
                        "任务结束时调用。参数说明：\n"
                        "- success: true=任务目标已完成，false=遇到阻碍无法继续\n"
                        "- result: success=true时填写完整最终结果（从截图中提取所需信息，"
                        "如有已保存笔记则一并整合），success=false时填写无法继续的原因"
                        "（如需要登录、验证码、权限不足、反复操作无效）"
                    ),
                ),
            },
        ),
    ),
    ToolDef(
        type=actions.DOUBLE_CLICK,
        prompt=ToolPromptDef(
            desc="双击目标元素（如选中文字、展开折叠项）",
            schema={
                "properties": {
                    "locate": prop("string", "目标元素的唯一性描述"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.RIGHT_CLICK,
        prompt=ToolPromptDef(
            desc="右键点击目标元素",
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "locate": prop("string", "目标元素的唯一性描述"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.HOVER,
        prompt=ToolPromptDef(
            desc="悬停在目标元素上以触发提示、下拉菜单等hover效果",
            platforms=[actions.PLATFORM_DESKTOP, actions.PLATFORM_CHROME],
            schema={
                "properties": {
                    "locate": prop("string", "元素的唯一性描述"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.DRAG,
        prompt=ToolPromptDef(
            desc="从起点拖拽到终点",
            schema={
                "properties": {
                    "startLocate": prop("string", "拖拽起点元素的唯一性描述"),
                    "endLocate": prop("string", "拖拽终点元素的唯一性描述"),
                },
                "required": ["startLocate", "endLocate"],
            },
        ),
    ),
    ToolDef(
        type=actions.LONG_PRESS,
        prompt=ToolPromptDef(
            desc="长按目标元素（触发上下文菜单、进入编辑/选择态、拖拽预备等）。仅触屏可用",
            platforms=[actions.PLATFORM_ANDROID, actions.PLATFORM_IOS],
            schema={
                "properties": {
                    "locate": prop("string", "目标元素的唯一性描述"),
                    "duration": prop("integer", "长按持续毫秒数，默认2000"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.MOUSE_DOWN,
        prompt=ToolPromptDef(
            desc=(
                "按住鼠标左键不放（拖拽起手、持续瞄准/蓄力等）。必须在后续步骤用 mouse_up 松开，"
                "否则之后所有点击都会变成拖拽。仅桌面可用"
            ),
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "locate": prop("string", "在哪个元素上按下鼠标的唯一性描述"),
                },
                "required": ["locate"],
            },
        ),
    ),
    ToolDef(
        type=actions.MOUSE_UP,
        prompt=ToolPromptDef(
            desc=(
                "松开鼠标左键，与 mouse_down 配对。locate 选填：填则移动到该元素再松开（拖拽到目标处），"
                "不填则在当前位置松开。仅桌面可用"
            ),
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "locate": prop("string", "松开位置的元素描述，不填则在当前光标处松开"),
                },
            },
        ),
    ),
    ToolDef(
        type=actions.KEY_DOWN,
        prompt=ToolPromptDef(
            desc=(
                "按住某个键不放（游戏里按住 w 持续移动、按住 shift 等）。必须在后续步骤用 key_up 松开，"
                "否则该键会一直生效。仅桌面可用"
            ),
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "key": prop("string", "按住的键名（如 w / space / shift / ctrl / Enter）"),
                },
                "required": ["key"],
            },
        ),
    ),
    ToolDef(
        type=actions.KEY_UP,
        prompt=ToolPromptDef(
            desc="松开之前用 key_down 按住的键。仅桌面可用",
            platforms=[actions.PLATFORM_DESKTOP],
            schema={
                "properties": {
                    "key": prop("string", "要松开的键名，需与 key_down 一致"),
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
        new_props[fx] = prop("integer", "x坐标")
        new_props[fy] = prop("integer", "y坐标")
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
        "reason": prop("string", "给出界面变化内容与详细决策理由"),
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
