"""LocalAISession: the multi-step ai() decision loop, one step per call.

Ports internal/task/act_service.go's session bootstrap + runAIDecision +
resolveCoordinates, minus everything multi-tenant (idempotency, billing,
Redis, heartbeats). One deliberate deviation from the server: token usage
accumulates across ALL attempts of a step, including discarded re-decides —
the user pays their own LLM bill, so under-reporting would lie to them.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from . import actions
from .engine import LocalEngine
from .history import History, HistoryConfig, default_history_config
from .prompts import NORMALIZED_COORD_RULE
from .coords import rescale_point
from .types import (
    Action,
    ConversationTurn,
    CustomToolDef,
    DecisionInput,
    DecisionResult,
    ModelConfig,
    TokenUsage,
    UnparsableResponseError,
)

logger = logging.getLogger("qirabot.engine")

# One initial decision plus at most one corrective re-decide — shared across
# BOTH failure kinds (unparsable response and unusable coordinates); the two
# together get a single retry, not one each.
MAX_GROUNDING_ATTEMPTS = 2

_UNPARSABLE_HINT = (
    "Your previous response was empty or not a valid tool call. "
    "Respond by calling exactly one of the provided tools."
)


class StepError(Exception):
    """A step-level failure returned to the caller as a failed StepResponse.
    finished mirrors the wire flag (True ends the SDK loop)."""

    def __init__(self, message: str, finished: bool = False) -> None:
        super().__init__(message)
        self.finished = finished


@dataclass
class StepOutcome:
    """The engine-side result of one ai() step (pre-wire)."""

    action_type: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    decision: str = ""
    output: str = ""
    finished: bool = False
    step_number: int = 0
    model_used: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    llm_decision_ms: int = 0
    coordinate_parse_ms: int = 0
    step_duration_ms: int = 0


def history_config_for_provider(cfg: ModelConfig | None, max_steps: int) -> HistoryConfig:
    """History sizing by provider. Claude benefits from a buffer large enough
    that prefix-cache truncation never fires mid-task, so the conversation
    cache prefix stays stable. (Cloud matched "claude"/"claude-vertex"; the
    anthropic direct provider should join this branch when it lands.)"""
    if cfg is not None and cfg.provider in ("claude", "claude-vertex"):
        return HistoryConfig(max_entries=max_steps * 2, max_screenshots=max_steps * 2)
    return default_history_config()


class LocalAISession:
    """State for one in-flight ai() command."""

    def __init__(
        self,
        instruction: str,
        platform: str,
        language: str,
        max_steps: int,
        model_config: ModelConfig | None,
        knowledge: str = "",
        custom_tools: list[CustomToolDef] | None = None,
        exclude_tools: list[str] | None = None,
        annotate_for_model: bool = False,
    ) -> None:
        self.instruction = instruction
        self.platform = platform
        self.language = language
        self.max_steps = max_steps
        self.model_config = model_config
        self.knowledge = knowledge
        self.custom_tools = custom_tools or []
        self.exclude_tools = exclude_tools or []
        self.annotate_for_model = annotate_for_model
        self.step_count = 0
        self.notes: list[str] = []
        self.history = History(history_config_for_provider(model_config, max_steps))
        # Cached screenshots for history replay, capped at the history
        # high-water mark (entries are batch-trimmed, so up to that many can
        # be retained at once).
        self.screenshots: list[bytes] = []
        self._custom_names = {d.name for d in self.custom_tools}

    def remember_screenshot(self, screenshot: bytes) -> None:
        if not screenshot:
            return
        self.screenshots.append(screenshot)
        # Cap at the history high-water mark so reverse alignment in
        # attach_screenshots always has a screenshot for every retained entry.
        window = self.history.high_water
        if len(self.screenshots) > window:
            self.screenshots = self.screenshots[-window:]

    def step(
        self,
        engine: LocalEngine,
        screenshot: bytes,
        action_result: str,
        device_width: int,
        device_height: int,
        is_first_step: bool,
    ) -> StepOutcome:
        """Run one decide→ground step. Mutates session state (step_count,
        history, notes). Raises StepError for step-level failures."""
        # Backfill the previous step's tool output with whatever the SDK
        # reported (arrives with the NEXT request, not at execution time).
        if not is_first_step and action_result:
            self.history.backfill_last_tool_output(action_result)
        self.history.attach_screenshots(self.screenshots)

        step_start = time.monotonic()
        hist_entries = self.history.entries()
        hist_summary = self.history.summary()

        usage = TokenUsage()
        llm_ms = 0
        coord_ms = 0
        correction_hint = ""
        act: Action | None = None
        result: DecisionResult | None = None
        finished = False
        is_save_note = False
        raw_params_json = ""

        attempt = 0
        while True:
            attempt += 1
            try:
                result = engine.decide(
                    DecisionInput(
                        instruction=self.instruction,
                        knowledge=self.knowledge,
                        platform=self.platform,
                        language=self.language,
                        current_screenshot=screenshot,
                        history=hist_entries,
                        is_first_step=is_first_step,
                        notes=self.notes,
                        summary=hist_summary,
                        model_config=self.model_config,
                        annotate_for_model=self.annotate_for_model,
                        correction_hint=correction_hint,
                        custom_tools=self.custom_tools,
                        exclude_tools=self.exclude_tools,
                    )
                )
            except UnparsableResponseError as exc:
                # The LLM call succeeded but the content was unusable —
                # transient; re-decide once with corrective feedback, same
                # budget as the grounding retry below.
                partial = getattr(exc, "result", None)
                if isinstance(partial, DecisionResult):
                    usage.add(partial.token_usage)
                    llm_ms += partial.duration_ms
                if attempt < MAX_GROUNDING_ATTEMPTS:
                    correction_hint = _UNPARSABLE_HINT
                    logger.warning(
                        "unparsable decision response; re-deciding (attempt %d): %s",
                        attempt,
                        exc,
                    )
                    continue
                # `usage` holds every attempt's spend — attach it so the
                # error payload carries the tokens (same as the grounding
                # path below; the SDK folds them into its session totals).
                err = StepError(str(exc))
                err.usage = usage  # type: ignore[attr-defined]
                raise err from exc

            usage.add(result.token_usage)
            llm_ms += result.duration_ms
            if result.action is None:
                err = StepError("AI returned no action")
                err.usage = usage  # type: ignore[attr-defined]
                raise err

            act = result.action
            finished = act.type == actions.DONE
            is_save_note = act.type == actions.SAVE_NOTE

            # History must replay the model's own normalized frame, captured
            # before grounding rewrites params to device pixels.
            raw_params_json = json.dumps(act.params, ensure_ascii=False)

            coord_start = time.monotonic()
            ground = _resolve_coordinates(
                act,
                device_width,
                device_height,
                finished=finished,
                is_save_note=is_save_note,
                is_custom=act.type in self._custom_names,
            )
            coord_ms += int((time.monotonic() - coord_start) * 1000)
            if ground is None:
                break
            # Unusable inline coordinates (the discarded attempt's tokens are
            # already in `usage` — deliberate deviation, see module docstring).
            if attempt >= MAX_GROUNDING_ATTEMPTS:
                msg = "inline grounding: model returned no usable coordinates"
                if act.type == actions.DRAG:
                    msg = "inline grounding: drag returned no usable coordinates"
                err = StepError(msg)
                err.usage = usage  # type: ignore[attr-defined]
                raise err
            correction_hint = ground
            logger.warning(
                "inline grounding produced unusable coordinates; re-deciding "
                "(attempt %d, action=%s)",
                attempt,
                act.type,
            )

        assert act is not None and result is not None

        # Accumulate note content after the loop, keyed on the committed
        # action, so a re-decide can't double-append.
        output = ""
        if is_save_note:
            content = act.params.get("content")
            if isinstance(content, str) and content:
                self.notes.append(content)
            output = "ok"

        if finished:
            r = act.params.get("result")
            output = r if isinstance(r, str) else ""

        self.step_count += 1
        entry = ConversationTurn(
            action_type=act.type,
            action_params=raw_params_json,
            reasoning=act.reasoning,
        )
        if is_save_note:
            entry.tool_output = "ok"
        self.history.add(entry)

        return StepOutcome(
            action_type=act.type,
            params=act.params,
            decision=act.reasoning,
            output=output,
            finished=finished,
            step_number=self.step_count,
            model_used=result.model_used,
            token_usage=usage,
            llm_decision_ms=llm_ms,
            coordinate_parse_ms=coord_ms,
            step_duration_ms=int((time.monotonic() - step_start) * 1000),
        )


def _resolve_coordinates(
    act: Action,
    dev_w: int,
    dev_h: int,
    finished: bool,
    is_save_note: bool,
    is_custom: bool,
) -> str | None:
    """Resolve target coordinates for a decided action, writing device pixels
    onto act.params. Returns None when resolved (or nothing to resolve), or
    the corrective hint string when the model must re-decide."""
    # Custom tools execute client-side and never carry locate/point fields
    # (reserved-name validation guarantees it); skip grounding outright.
    if finished or is_save_note or is_custom or not act.params:
        return None

    if act.type == actions.DRAG:
        # Drag carries two model-emitted points; resolve both endpoints into
        # the start_/end_ coords the SDK dispatcher reads.
        sx, sy, s_mode = rescale_point(
            act.params.get("start_point_x"), act.params.get("start_point_y"), dev_w, dev_h
        )
        ex, ey, e_mode = rescale_point(
            act.params.get("end_point_x"), act.params.get("end_point_y"), dev_w, dev_h
        )
        if not s_mode or not e_mode:
            return (
                "The coordinates from the previous drag are unusable (start={},{} end={},{}). "
            "{}. Re-examine the screenshot and try again.".format(
                    act.params.get("start_point_x"),
                    act.params.get("start_point_y"),
                    act.params.get("end_point_x"),
                    act.params.get("end_point_y"),
                    NORMALIZED_COORD_RULE,
                )
            )
        if s_mode == "pixel" or e_mode == "pixel":
            logger.warning("inline grounding salvaged raw pixel coordinates (drag)")
        act.params["start_x"] = sx
        act.params["start_y"] = sy
        act.params["end_x"] = ex
        act.params["end_y"] = ey
        # Anchor the report crosshair at the drag's start point.
        act.params["x"] = sx
        act.params["y"] = sy
        for k in ("start_point_x", "start_point_y", "end_point_x", "end_point_y"):
            act.params.pop(k, None)
        return None

    locate = act.params.get("locate")
    has_px = "point_x" in act.params
    has_py = "point_y" in act.params
    # A targeting action carries point fields. A partial point (one axis)
    # goes through the retry path rather than silently dispatching without
    # coordinates; a bare locate string (schema has no such field, but a
    # hallucination can emit one) is treated the same way.
    is_targeting = bool(locate) or has_px or has_py
    if not is_targeting:
        return None  # non-targeting action (scroll/wait/etc.)

    x, y, mode = rescale_point(
        act.params.get("point_x"), act.params.get("point_y"), dev_w, dev_h
    )
    if not mode:
        return (
            "The previously output point_x={}, point_y={} are unusable. "
            "{}. Re-examine the screenshot and output point_x and point_y again.".format(
                act.params.get("point_x"), act.params.get("point_y"), NORMALIZED_COORD_RULE
            )
        )
    if mode == "pixel":
        logger.warning("inline grounding salvaged raw pixel coordinates: %d,%d", x, y)
    act.params["x"] = x
    act.params["y"] = y
    act.params.pop("point_x", None)
    act.params.pop("point_y", None)
    return None
