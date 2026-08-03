"""LocalEngine: the four LLM entry points (decide / extract /
check_condition / locate).

Mirrors internal/decision/engine.go + locate.go. Response parsing prefers a
tool call; a text-only reply falls back to JSON extraction, and an
unparsable reply raises UnparsableResponseError so the session layer can
re-decide with corrective feedback (same contract as the Go engine).
"""

from __future__ import annotations

import io
import json
import logging
import time
from typing import Any

from .coords import bbox_center, rescale_point, to_float
from .prompts import (
    LOCATE_SYSTEM_PROMPT,
    build_conversation_messages,
    build_system_prompts,
    get_language_display_name,
)
from .custom_tools import custom_tool_definitions, filter_excluded
from .providers.base import ChatRequest, Provider
from .tools import prop, tool_definitions_for_platform
from .types import (
    LOCATE_FORMAT_BBOX,
    Action,
    ConditionInput,
    ConditionResult,
    DecisionInput,
    DecisionResult,
    ExtractInput,
    ExtractResult,
    Image,
    LocateInput,
    LocateResult,
    LocateUnparsableError,
    Message,
    ModelConfig,
    ToolDefinition,
    UnparsableResponseError,
    UnsupportedScreenshotError,
    detect_image_mime,
)

logger = logging.getLogger("qirabot.engine")

DECIDE_TIMEOUT = 120.0
# Deliberately tighter than decide/extract: the SDK's whole-step budget is
# finite and the session layer may retry a locate once, so a single shot must
# leave room for the second attempt.
LOCATE_TIMEOUT = 60.0


class LocalEngine:
    """Orchestrates AI-driven action decisions against a bound provider."""

    def __init__(self, provider: Provider, model: str = "") -> None:
        self._provider = provider
        self._model = model

    def decide(self, input: DecisionInput) -> DecisionResult:
        """Analyze the current screenshot and history, return one action.
        Raises UnparsableResponseError (with .result carrying the real token
        spend) when the reply has no tool call and no parseable JSON."""
        start = time.monotonic()
        _validate_decide_input(input)

        model, params = self._resolve_model_params(input.model_config)

        messages = build_conversation_messages(input)
        tools = tool_definitions_for_platform(input.platform)
        tools = filter_excluded(tools, input.exclude_tools)
        tools = tools + custom_tool_definitions(input.custom_tools)

        cacheable, dynamic = build_system_prompts(
            input.platform,
            input.instruction,
            input.knowledge,
            input.language,
            input.annotate_for_model,
            input.exclude_tools,
        )

        resp = self._provider.chat(
            ChatRequest(
                model=model,
                messages=messages,
                tools=tools,
                force_tool=True,
                cacheable_system_prompt=cacheable,
                system_prompt=dynamic,
                params=params,
            ),
            timeout=DECIDE_TIMEOUT,
        )

        duration_ms = int((time.monotonic() - start) * 1000)

        if resp.tool_calls:
            tc = resp.tool_calls[0]
            result_action = Action(
                type=tc.name,
                params=filter_meta_fields(tc.args),
                reasoning=string_from_args(tc.args, "reason"),
            )
        else:
            # Fallback: parse from text JSON response.
            try:
                name, args, reasoning = parse_response(resp.content)
            except ValueError as exc:
                # finish_reason distinguishes the empty-response causes (e.g.
                # Gemini malformed_function_call vs safety block vs length).
                logger.warning(
                    "failed to parse decision response: %s (finish_reason=%s, "
                    "thinking_len=%d, duration_ms=%d)",
                    exc,
                    resp.finish_reason,
                    len(resp.thinking),
                    duration_ms,
                )
                partial = DecisionResult(
                    token_usage=resp.token_usage,
                    duration_ms=duration_ms,
                    model_used=resp.model_used,
                    raw_response=resp.content,
                )
                err = UnparsableResponseError(f"parse decision response: {exc}")
                err.result = partial  # type: ignore[attr-defined]
                raise err from exc
            result_action = Action(type=name, params=args, reasoning=reasoning)

        logger.info(
            "decision completed: action=%s model=%s duration_ms=%d tokens=%d/%d",
            result_action.type,
            resp.model_used,
            duration_ms,
            resp.token_usage.input_tokens,
            resp.token_usage.output_tokens,
        )
        return DecisionResult(
            action=result_action,
            token_usage=resp.token_usage,
            duration_ms=duration_ms,
            model_used=resp.model_used,
            raw_response=resp.content,
        )

    def extract(self, input: ExtractInput) -> ExtractResult:
        """Extract structured data from a screenshot."""
        start = time.monotonic()
        if not input.prompt:
            raise ValueError("extract: prompt is required")
        if not input.screenshot:
            raise ValueError("extract: screenshot is required")

        model, params = self._resolve_model_params(input.model_config)
        lang_name = get_language_display_name(input.language)

        resp = self._provider.chat(
            ChatRequest(
                model=model,
                messages=[
                    Message(
                        role="user",
                        content=(
                            "Extract the following information from the screenshot: "
                            f"{input.prompt}\n\nRespond in {lang_name}."
                        ),
                    ),
                    Message(
                        role="user",
                        images=[
                            Image(
                                mime_type=detect_image_mime(input.screenshot),
                                data=input.screenshot,
                            )
                        ],
                    ),
                ],
                tools=[
                    ToolDefinition(
                        name="extract_result",
                        description="Return the extracted information",
                        parameters={
                            "type": "object",
                            "properties": {
                                "result": prop(
                                    "string",
                                    "ONLY the extracted data, no explanations. "
                                    "Follow the user's requested format exactly",
                                ),
                            },
                            "required": ["result"],
                        },
                    )
                ],
                force_tool=True,
                system_prompt=(
                    "You are a data extraction assistant. Analyze the screenshot and "
                    "extract the requested information. Put ONLY the extracted data in "
                    "the 'result' field — no explanations, no preamble, just the raw "
                    "data in the format the user requested."
                ),
                params=params,
            ),
            timeout=DECIDE_TIMEOUT,
        )

        result = ""
        if resp.tool_calls:
            result = string_from_args(resp.tool_calls[0].args, "result")

        return ExtractResult(
            result=result,
            token_usage=resp.token_usage,
            duration_ms=int((time.monotonic() - start) * 1000),
            model_used=resp.model_used,
        )

    def check_condition(self, input: ConditionInput) -> ConditionResult:
        """Determine whether a visual condition is met."""
        start = time.monotonic()
        if not input.condition:
            raise ValueError("check_condition: condition is required")
        if not input.screenshot:
            raise ValueError("check_condition: screenshot is required")

        model, params = self._resolve_model_params(input.model_config)
        lang_name = get_language_display_name(input.language)

        resp = self._provider.chat(
            ChatRequest(
                model=model,
                messages=[
                    Message(
                        role="user",
                        content=(
                            "Check if this condition is currently met on the screen: "
                            f"{input.condition}\n\nRespond in {lang_name}."
                        ),
                    ),
                    Message(
                        role="user",
                        images=[
                            Image(
                                mime_type=detect_image_mime(input.screenshot),
                                data=input.screenshot,
                            )
                        ],
                    ),
                ],
                tools=[
                    ToolDefinition(
                        name="check_result",
                        description="Report whether the condition is met",
                        parameters={
                            "type": "object",
                            "properties": {
                                "reason": prop("string", "Explanation of the assessment"),
                                "condition_met": {
                                    "type": "boolean",
                                    "description": "true if the condition is met, false otherwise",
                                },
                            },
                            "required": ["reason", "condition_met"],
                        },
                    )
                ],
                force_tool=True,
                system_prompt=(
                    "You are a visual condition checker. Analyze the screenshot and "
                    "determine whether the specified condition is currently met. Call "
                    "the check_result tool with your assessment."
                ),
                params=params,
            ),
            timeout=DECIDE_TIMEOUT,
        )

        met = False
        reasoning = ""
        if resp.tool_calls:
            args = resp.tool_calls[0].args
            reasoning = string_from_args(args, "reason")
            v = args.get("condition_met")
            if isinstance(v, bool):
                met = v

        return ConditionResult(
            met=met,
            reasoning=reasoning,
            token_usage=resp.token_usage,
            duration_ms=int((time.monotonic() - start) * 1000),
            model_used=resp.model_used,
        )

    def locate(self, input: LocateInput) -> LocateResult:
        """Find one UI element on a screenshot from a natural-language
        description. Raises UnsupportedScreenshotError before any LLM call
        (zero spend) for undecodable screenshots, and LocateUnparsableError
        (result attached — the spend is real) for unusable model output."""
        start = time.monotonic()
        if not input.locate:
            raise ValueError("locate: description is required")
        if not input.screenshot:
            raise ValueError("locate: screenshot is required")

        width, height = _decode_dimensions(input.screenshot)

        fmt = "point"
        if input.model_config is not None and input.model_config.locate_format == LOCATE_FORMAT_BBOX:
            fmt = "bbox"

        model, params = self._resolve_model_params(input.model_config)

        resp = self._provider.chat(
            ChatRequest(
                model=model,
                messages=[
                    Message(role="user", content="Find: " + input.locate),
                    Message(
                        role="user",
                        images=[
                            Image(
                                mime_type=detect_image_mime(input.screenshot),
                                data=input.screenshot,
                            )
                        ],
                    ),
                ],
                tools=[locate_tool_definition(fmt, input.language)],
                force_tool=True,
                system_prompt=LOCATE_SYSTEM_PROMPT,
                params=params,
            ),
            timeout=LOCATE_TIMEOUT,
        )

        result = LocateResult(
            token_usage=resp.token_usage,
            duration_ms=int((time.monotonic() - start) * 1000),
            model_used=resp.model_used,
        )

        if not resp.tool_calls:
            raise LocateUnparsableError(
                f"locate response unusable: no tool call in response "
                f"(finish_reason={resp.finish_reason})",
                result,
            )
        args = resp.tool_calls[0].args

        found = args.get("found")
        if isinstance(found, bool) and not found:
            result.not_found_reason = string_from_args(args, "error")
            logger.info(
                "locate: element not found: %s (%s)", input.locate, result.not_found_reason
            )
            return result

        coords = parse_locate_coords(args, fmt, width, height)
        if coords is None:
            raise LocateUnparsableError(
                f"locate response unusable: unusable coordinates in {fmt} args", result
            )
        result.found, result.x, result.y = True, coords[0], coords[1]
        return result

    def _resolve_model_params(
        self, cfg: ModelConfig | None
    ) -> tuple[str, dict[str, Any]]:
        model = self._model
        params: dict[str, Any] = {"temperature": 0.2, "max_tokens": 4096}
        if cfg is not None:
            if cfg.model:
                model = cfg.model
            params.update(cfg.params)
        return model, params


def _validate_decide_input(input: DecisionInput) -> None:
    if not input.instruction:
        raise ValueError("invalid decision input: instruction is required")
    if not input.current_screenshot and not input.is_first_step:
        raise ValueError(
            "invalid decision input: current screenshot is required (except for first step)"
        )


def _decode_dimensions(screenshot: bytes) -> tuple[int, int]:
    """Screenshot dimensions from the image header only (drives the
    normalized→pixel conversion). Pillow decodes webp too, so unlike the Go
    server the local engine accepts it."""
    try:
        from PIL import Image as PILImage

        with PILImage.open(io.BytesIO(screenshot)) as img:
            width, height = img.size
    except Exception as exc:
        raise UnsupportedScreenshotError(
            f"screenshot format not supported for locate: {exc}"
        ) from exc
    if width <= 0 or height <= 0:
        raise UnsupportedScreenshotError(
            "screenshot format not supported for locate: empty dimensions"
        )
    return width, height


def locate_tool_definition(fmt: str, language: str) -> ToolDefinition:
    """The report_location schema for the given coordinate dialect. The
    dialect only changes how the model states the position; found/error are
    shared."""
    props: dict[str, Any] = {
        "found": prop("boolean", "true when the described element is visible in the screenshot"),
        "error": prop(
            "string",
            "required when found=false: short reason, e.g. what is visible instead. "
            "Respond in " + get_language_display_name(language),
        ),
    }
    if fmt == "bbox":
        props["box_2d"] = {
            "type": "array",
            "items": {"type": "integer"},
            "description": (
                "bounding box of the target as [ymin, xmin, ymax, xmax], each an "
                "integer normalized to 0-1000 (origin top-left)"
            ),
        }
    else:
        props["point_x"] = prop(
            "integer",
            "horizontal center of the target, an integer normalized to 0-1000 "
            "(0 = left edge, 1000 = right edge)",
        )
        props["point_y"] = prop(
            "integer",
            "vertical center of the target, an integer normalized to 0-1000 "
            "(0 = top edge, 1000 = bottom edge)",
        )
    return ToolDefinition(
        name="report_location",
        description=(
            "Report the location of the described element, or found=false when it "
            "is not visible"
        ),
        parameters={"type": "object", "properties": props, "required": ["found"]},
    )


def parse_locate_coords(
    args: dict[str, Any], fmt: str, w: int, h: int
) -> tuple[int, int] | None:
    """Convert dialect-specific tool args into screenshot pixels. The point
    dialect mirrors the act flow's inline contract via rescale_point,
    including its raw-pixel salvage. The bbox dialect declares 0–1000 in its
    schema, so any out-of-range value is rejected outright — the salvage
    heuristic's premise ("the model may have emitted pixels") does not apply
    to an explicitly normalized contract."""
    if fmt == "bbox":
        box = args.get("box_2d")
        if not isinstance(box, list) or len(box) != 4:
            return None
        vals: list[float] = []
        for v in box:
            f = to_float(v)
            if f is None or f < 0 or f > 1000:
                return None
            vals.append(f)
        ymin, xmin, ymax, xmax = vals
        if ymax < ymin or xmax < xmin:
            return None
        return bbox_center(vals, w, h)

    x, y, mode = rescale_point(args.get("point_x"), args.get("point_y"), w, h)
    return (x, y) if mode else None


def filter_meta_fields(args: dict[str, Any]) -> dict[str, Any]:
    """Copy of args without the common meta-fields."""
    return {k: v for k, v in args.items() if k not in ("reason", "safety_decision")}


def string_from_args(args: dict[str, Any], key: str) -> str:
    v = args.get(key)
    return v if isinstance(v, str) else ""


def parse_response(content: str) -> tuple[str, dict[str, Any], str]:
    """Extract (name, params, reasoning) from a text JSON response.
    Raises ValueError when no usable JSON is found."""
    cleaned = extract_json(content)
    if not cleaned:
        raise ValueError("no JSON found in response")
    try:
        data = json.loads(cleaned)
    except ValueError as exc:
        raise ValueError(f"unmarshal response: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("unmarshal response: not an object")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("missing action name in response")
    raw_input = data.get("input")
    args = raw_input if isinstance(raw_input, dict) else {}
    reason = data.get("reason")
    return name, args, reason if isinstance(reason, str) else ""


def extract_json(s: str) -> str:
    """Find the first JSON object in the string: markdown code fence first,
    then a bare brace-balanced object."""
    if "```" in s:
        after_fence = s.split("```", 1)[1]
        if "\n" in after_fence:
            content = after_fence.split("\n", 1)[1]
            if "```" in content:
                return content.split("```", 1)[0].strip()

    brace_start = -1
    depth = 0
    for i, c in enumerate(s):
        if c == "{":
            if brace_start == -1:
                brace_start = i
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and brace_start != -1:
                return s[brace_start : i + 1]
    return ""
