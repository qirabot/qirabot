"""Custom tool support for bot.ai(): build wire definitions from callables.

The engine never executes custom tools — it merges the definitions into the
LLM tool list and, when the model picks one, surfaces its name/params in the
step outcome. The SDK dispatches to the registered handler and feeds the
return value back via ``action_result`` on the next step.
"""

from __future__ import annotations

import inspect
import re
from typing import Any, Callable

_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_TOOLS = 16
_MAX_DESC_LEN = 1024

# Python annotation -> JSON Schema type. Unannotated/unknown falls back to
# string, which every provider accepts and the handler can coerce.
_TYPE_MAP: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}


def build_tool_defs(
    custom_tools: list[Callable[..., Any] | dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Callable[..., Any]]]:
    """Convert ``custom_tools`` into wire definitions plus a dispatch table.

    Accepts plain callables (name/description/parameters introspected from the
    function) or dicts carrying an explicit schema alongside a ``handler``
    callable (escape hatch for schemas introspection can't express, e.g.
    enums or per-parameter descriptions). Raises ``ValueError`` on invalid
    input so mistakes surface before any model call; the engine applies the
    same rules (plus collision/reserved-name checks) authoritatively in
    :func:`qirabot.engine.custom_tools.parse_custom_tools`.
    """
    if len(custom_tools) > _MAX_TOOLS:
        raise ValueError(f"custom_tools: at most {_MAX_TOOLS} tools allowed, got {len(custom_tools)}")

    defs: list[dict[str, Any]] = []
    handlers: dict[str, Callable[..., Any]] = {}
    for tool in custom_tools:
        if callable(tool):
            tool_def, handler = _def_from_callable(tool)
        elif isinstance(tool, dict):
            tool_def, handler = _def_from_dict(tool)
        else:
            raise ValueError(f"custom_tools: entries must be callables or dicts, got {type(tool).__name__}")

        name = tool_def["name"]
        if not _TOOL_NAME_RE.match(name):
            raise ValueError(f"custom tool {name!r}: name must match ^[a-z][a-z0-9_]{{0,63}}$")
        if not tool_def["description"]:
            raise ValueError(f"custom tool {name!r}: description is required (add a docstring)")
        if len(tool_def["description"]) > _MAX_DESC_LEN:
            raise ValueError(f"custom tool {name!r}: description exceeds {_MAX_DESC_LEN} characters")
        if name in handlers:
            raise ValueError(f"custom tool {name!r}: duplicate name")

        defs.append(tool_def)
        handlers[name] = handler
    return defs, handlers


def _def_from_callable(fn: Callable[..., Any]) -> tuple[dict[str, Any], Callable[..., Any]]:
    name = getattr(fn, "__name__", "")
    if name == "<lambda>":
        raise ValueError("custom_tools: lambdas have no usable name; use a named function or a dict")
    doc = inspect.getdoc(fn) or ""

    properties: dict[str, Any] = {}
    required: list[str] = []
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # builtins without introspectable signatures
        sig = None
    if sig is not None:
        for param in sig.parameters.values():
            if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
                raise ValueError(
                    f"custom tool {name!r}: *args/**kwargs are not supported; declare explicit parameters"
                )
            properties[param.name] = {"type": _TYPE_MAP.get(param.annotation, "string")}
            if param.default is param.empty:
                required.append(param.name)

    tool_def: dict[str, Any] = {"name": name, "description": doc}
    if properties:
        parameters: dict[str, Any] = {"properties": properties}
        if required:
            parameters["required"] = required
        tool_def["parameters"] = parameters
    return tool_def, fn


def _def_from_dict(d: dict[str, Any]) -> tuple[dict[str, Any], Callable[..., Any]]:
    handler = d.get("handler")
    if not callable(handler):
        raise ValueError(
            f"custom tool {d.get('name')!r}: dict form requires a callable 'handler' entry"
        )
    tool_def: dict[str, Any] = {
        "name": d.get("name", "") or "",
        "description": d.get("description", "") or "",
    }
    if d.get("parameters") is not None:
        tool_def["parameters"] = d["parameters"]
    return tool_def, handler
