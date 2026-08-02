"""User-registered custom tools, exclude_tools and knowledge validation.

Mirrors internal/decision/custom_tools.go + knowledge.go. Error messages
describe the caller's own input verbatim (kept aligned with the Go server so
existing SDK tests and user-facing docs stay accurate).
"""

from __future__ import annotations

import json
import re
from typing import Any

from . import actions
from .tools import (
    LOCATE_POINT_FIELDS,
    TOOL_REGISTRY,
    available_for_platform,
    merge_common_fields,
)
from .types import CustomToolDef, ToolDefinition

MAX_CUSTOM_TOOLS = 16
MAX_CUSTOM_TOOL_DESC_LEN = 1024
MAX_CUSTOM_TOOLS_BYTES = 16 * 1024
MAX_KNOWLEDGE_BYTES = 32 * 1024

_CUSTOM_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _reserved_tool_names() -> set[str]:
    """Every name a custom tool may not use: all registry action types
    (LLM-visible or not), the direct-action wire types, and the engine's
    internal tool names for extract/condition calls."""
    names = {
        "ai",  # /act wire type for the AI decision path
        actions.AI_DECISION,
        actions.EXTRACT,
        actions.ASSERT,
        actions.WAIT_FOR,
        "extract_result",  # engine-internal tool name (extract)
        "check_result",  # engine-internal tool name (check_condition)
    }
    names.update(t.type for t in TOOL_REGISTRY)
    return names


def _reserved_property_names() -> set[str]:
    """Schema property names custom tools may not declare: meta fields
    injected/stripped by the engine, locate-style fields consumed by
    grounding, and the coordinate output keys resolve_coordinates writes into
    params (SDK report crosshairs read them back)."""
    names = {
        "reason",
        "safety_decision",
        "x",
        "y",
        "start_x",
        "start_y",
        "end_x",
        "end_y",
    }
    for locate, fx, fy in LOCATE_POINT_FIELDS:
        names.update((locate, fx, fy))
    return names


RESERVED_TOOL_NAMES = _reserved_tool_names()
RESERVED_PROPERTY_NAMES = _reserved_property_names()


def parse_custom_tools(raw: Any) -> list[CustomToolDef]:
    """Decode and validate the raw `custom_tools` value. Returns normalized
    definitions: parameters keep only `properties` and `required`.
    Raises ValueError with a client-safe message."""
    if not isinstance(raw, list):
        raise ValueError("must be an array of tool definitions")
    if not raw:
        return []
    if len(raw) > MAX_CUSTOM_TOOLS:
        raise ValueError(f"at most {MAX_CUSTOM_TOOLS} tools allowed, got {len(raw)}")

    defs: list[CustomToolDef] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"tool #{i + 1}: must be an object")
        name = item.get("name")
        if not isinstance(name, str) or not _CUSTOM_TOOL_NAME_RE.match(name):
            shown = name if isinstance(name, str) else ""
            raise ValueError(
                f'tool #{i + 1}: name "{shown}" must match ^[a-z][a-z0-9_]{{0,63}}$'
            )
        if name in RESERVED_TOOL_NAMES:
            raise ValueError(f'tool "{name}": name conflicts with a built-in tool')
        if name in seen:
            raise ValueError(f'tool "{name}": duplicate name')
        seen.add(name)

        desc = item.get("description")
        if not isinstance(desc, str) or desc == "":
            raise ValueError(f'tool "{name}": description is required')
        if len(desc) > MAX_CUSTOM_TOOL_DESC_LEN:
            raise ValueError(
                f'tool "{name}": description exceeds {MAX_CUSTOM_TOOL_DESC_LEN} characters'
            )

        params = _normalize_tool_parameters(name, item.get("parameters"))
        defs.append(CustomToolDef(name=name, description=desc, parameters=params))

    encoded = json.dumps(
        [
            {"name": d.name, "description": d.description, "parameters": d.parameters}
            for d in defs
        ],
        ensure_ascii=False,
    ).encode("utf-8")
    if len(encoded) > MAX_CUSTOM_TOOLS_BYTES:
        raise ValueError(f"tool definitions exceed {MAX_CUSTOM_TOOLS_BYTES} bytes total")
    return defs


def _normalize_tool_parameters(tool: str, raw: Any) -> dict[str, Any] | None:
    """Validate a tool's `parameters` value and reduce it to the two keys the
    engine honors."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f'tool "{tool}": parameters must be an object')

    out: dict[str, Any] = {}
    props = raw.get("properties")
    if "properties" in raw:
        if not isinstance(props, dict):
            raise ValueError(f'tool "{tool}": parameters.properties must be an object')
        for prop_name, prop_val in props.items():
            if prop_name in RESERVED_PROPERTY_NAMES:
                raise ValueError(f'tool "{tool}": property name "{prop_name}" is reserved')
            if not isinstance(prop_val, dict):
                raise ValueError(f'tool "{tool}": property "{prop_name}" must be an object')
        if props:
            out["properties"] = props

    if "required" in raw:
        raw_req = raw["required"]
        if not isinstance(raw_req, list):
            raise ValueError(f'tool "{tool}": parameters.required must be an array of strings')
        props_map = out.get("properties")
        declared = props_map if isinstance(props_map, dict) else {}
        required: list[str] = []
        for r in raw_req:
            if not isinstance(r, str):
                raise ValueError(
                    f'tool "{tool}": parameters.required must be an array of strings'
                )
            if r not in declared:
                raise ValueError(
                    f'tool "{tool}": required field "{r}" is not declared in properties'
                )
            required.append(r)
        if required:
            out["required"] = required

    if not out:
        return None
    return out


def parse_exclude_tools(raw: Any, platform: str) -> list[str]:
    """Validate the raw `exclude_tools` value: every entry must be an
    LLM-visible built-in tool on the given platform (excluding done, which
    the loop needs to terminate). Unknown names, non-LLM actions, and tools
    of other platforms are rejected rather than silently ignored. Returns the
    list deduplicated, order preserved."""
    if not isinstance(raw, list):
        raise ValueError("must be an array of tool names")

    visible = {
        t.type
        for t in TOOL_REGISTRY
        if t.prompt is not None and available_for_platform(t.prompt, platform)
    }

    names: list[str] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, str):
            raise ValueError(f"entry #{i + 1}: must be a string")
        if item == actions.DONE:
            raise ValueError(f'tool "{actions.DONE}" cannot be excluded')
        if item not in visible:
            raise ValueError(f'unknown or unavailable tool "{item}" for platform "{platform}"')
        if item in seen:
            continue
        seen.add(item)
        names.append(item)
    return names


def parse_knowledge(raw: Any) -> str:
    """Validate the raw `knowledge` value. Like custom tools, knowledge is
    registered on the first step of the ai command and immutable afterwards."""
    if not isinstance(raw, str):
        raise ValueError("must be a string")
    size = len(raw.encode("utf-8"))
    if size > MAX_KNOWLEDGE_BYTES:
        raise ValueError(f"exceeds {MAX_KNOWLEDGE_BYTES} bytes (got {size})")
    return raw


def custom_tool_names(defs: list[CustomToolDef]) -> set[str]:
    """Name set of defs for O(1) membership tests."""
    return {d.name for d in defs}


def custom_tool_definitions(defs: list[CustomToolDef]) -> list[ToolDefinition]:
    """Convert defs to LLM tool definitions, preserving client order (appended
    after built-ins so the prompt-cache prefix stays stable within a session).
    The common reason field is injected like for built-ins; locate/point-field
    grounding transforms never apply."""
    return [
        ToolDefinition(
            name=d.name,
            description=d.description,
            parameters=merge_common_fields(d.parameters),
        )
        for d in defs
    ]


def filter_excluded(tools: list[ToolDefinition], exclude: list[str]) -> list[ToolDefinition]:
    """Remove the named tools from the built-in definition list. exclude is
    pre-validated by parse_exclude_tools (never contains done)."""
    if not exclude:
        return tools
    return [t for t in tools if t.name not in exclude]
