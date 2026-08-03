"""LocalBackend: the in-process replacement for the cloud /act endpoint.

Accepts the same request dict the SDK has always built and returns the same
response dict shape (actionType/params/finished/inputTokens/...), so the
client loop, report and token accounting consume it unchanged. Everything
multi-tenant (step_seq idempotency, billing, heartbeats, control plane) is
gone — a local process needs none of it.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Any

import httpx

from . import actions
from .engine import LocalEngine
from .providers.base import Provider, ProviderError
from .providers.registry import (
    PROVIDER_GEMINI,
    ModelSpec,
    create_provider,
    parse_model,
    resolve_gemini_api_key,
    resolve_vertex_api_key,
    resolve_vertex_location,
    resolve_vertex_project,
)
from .providers.vertex_auth import VertexTokenSource
from .custom_tools import parse_custom_tools, parse_exclude_tools, parse_knowledge
from .session import LocalAISession, StepError, StepOutcome
from .types import (
    ConditionInput,
    CustomToolDef,
    ExtractInput,
    LocateInput,
    LocateResult,
    LocateUnparsableError,
    ModelConfig,
    TokenUsage,
    UnsupportedScreenshotError,
)

logger = logging.getLogger("qirabot.engine")

# Action types resolved by a single VLM locate call (mirrors the server's
# act_handler routing).
_LOCATE_ACTIONS = frozenset(
    {
        actions.CLICK,
        actions.DOUBLE_CLICK,
        actions.RIGHT_CLICK,
        actions.HOVER,
        actions.TYPE_TEXT,
        actions.LONG_PRESS,
        actions.MOUSE_DOWN,
        actions.MOUSE_UP,
        actions.LOCATE,
        actions.CLEAR_TEXT,
        actions.SCROLL_AT,
    }
)

_CONDITION_ACTIONS = frozenset({actions.ASSERT, actions.WAIT_FOR})


class LocalBackend:
    """One backend per Qirabot instance; a single ai() session slot (the SDK
    loop is synchronous, so at most one ai command is in flight)."""

    def __init__(
        self,
        model: str,
        vertex_project: str = "",
        vertex_location: str = "",
        vertex_api_key: str = "",
        gemini_api_key: str = "",
        thinking_level: str = "",
        media_resolution: str = "",
        locate_format: str = "",
        annotate_for_model: bool = False,
        provider: Provider | None = None,
    ) -> None:
        self._spec: ModelSpec = parse_model(model)
        self._thinking_level = thinking_level
        self._media_resolution = media_resolution
        self._locate_format = locate_format
        self._annotate_for_model = annotate_for_model
        self._session: LocalAISession | None = None
        self._http: httpx.Client | None = None
        self._trace: _Tracer | _NullTracer = _Tracer.from_env() or _NullTracer()

        if provider is None and self._spec.provider == PROVIDER_GEMINI:
            # Gemini Developer API (AI Studio keys): no ADC, no
            # project/location — the key is the whole auth story.
            # create_provider raises the actionable error when no key
            # resolves (param > QIRA_GEMINI_API_KEY > GEMINI_API_KEY).
            self._http = httpx.Client()
            try:
                provider = create_provider(
                    self._spec,
                    "",
                    "",
                    None,
                    self._http,
                    api_key=resolve_gemini_api_key(gemini_api_key),
                )
            except ValueError:
                self._http.close()
                self._http = None
                raise
            logger.info(
                "local engine: model=%s/%s auth=api-key endpoint=generativelanguage",
                self._spec.provider,
                self._spec.model,
            )
        elif provider is None:
            # gemini-vertex: a configured API key (param > QIRA_VERTEX_API_KEY)
            # always wins over ADC — the variable is qirabot-scoped, so
            # setting it is a deliberate choice, unlike project/location
            # vars, which commonly linger from an ADC-era setup.
            api_key = resolve_vertex_api_key(vertex_api_key)
            if api_key:
                if vertex_project.strip() or vertex_location.strip():
                    logger.info(
                        "vertex_project/vertex_location ignored: API-key auth "
                        "is project-bound and global-endpoint only"
                    )
                self._http = httpx.Client()
                provider = create_provider(
                    self._spec, "", "", None, self._http, api_key=api_key
                )
                logger.info(
                    "local engine: model=%s/%s auth=api-key endpoint=global",
                    self._spec.provider,
                    self._spec.model,
                )
            else:
                tokens = VertexTokenSource()
                location = resolve_vertex_location(vertex_location)
                project = resolve_vertex_project(vertex_project, tokens)
                self._http = httpx.Client()
                provider = create_provider(
                    self._spec, project, location, tokens, self._http
                )
                logger.info(
                    "local engine: model=%s/%s project=%s location=%s",
                    self._spec.provider,
                    self._spec.model,
                    project,
                    location,
                )
        self._engine = LocalEngine(provider, self._spec.model)

    @property
    def model_label(self) -> str:
        return f"{self._spec.provider}/{self._spec.model}"

    def close(self) -> None:
        self._session = None
        if self._http is not None:
            self._http.close()
            self._http = None

    # -- /act semantics ------------------------------------------------

    def act(
        self, screenshot: bytes, request: dict[str, Any], screenshot_mime: str = ""
    ) -> dict[str, Any]:
        action = request.get("action") or {}
        action_type = str(action.get("type") or "")
        params = action.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        try:
            if action_type == "ai":
                resp = self._handle_ai(screenshot, request, params)
            elif action_type in _LOCATE_ACTIONS:
                resp = self._handle_locate(screenshot, request, action_type, params)
            elif action_type == actions.EXTRACT:
                resp = self._handle_extract(screenshot, request, params)
            elif action_type in _CONDITION_ACTIONS:
                resp = self._handle_condition(screenshot, request, params)
            else:
                resp = _error_payload(f"unsupported action type: {action_type}", False)
        except ProviderError as exc:
            resp = _error_payload(str(exc), False)

        self._trace.record(action_type, screenshot, request, resp)
        return resp

    # -- model config --------------------------------------------------

    def _model_config(self, request: dict[str, Any]) -> ModelConfig:
        # Engine-level defaults, mirroring what the v2 cloud aliases always
        # sent. Without them the provider ports' zero-value fallbacks apply:
        # temperature 0.0 (Gemini 3 degrades below its recommended 1.0) and
        # the API-side media resolution. Screenshots default to "high" — UI
        # text is dense and the decision quality is the product.
        params: dict[str, Any] = {
            "temperature": 1.0,
            "media_resolution": self._media_resolution or "high",
        }
        # thinking_level: per-request override > constructor > "low". The
        # explicit "low" floor matches every v2 cloud alias (fast ran
        # minimal); leaving it unset would drift with the API-side default,
        # which for Gemini 3 is high — slower and pricier on every step.
        tl = request.get("thinking_level")
        if isinstance(tl, str) and tl:
            params["thinking_level"] = tl
        else:
            params["thinking_level"] = self._thinking_level or "low"
        return ModelConfig(
            provider=self._spec.provider,
            model=self._spec.model,
            params=params,
            locate_format=self._locate_format,
        )

    @staticmethod
    def _language(request: dict[str, Any]) -> str:
        lang = request.get("language")
        return lang if isinstance(lang, str) else ""

    @staticmethod
    def _device_info(request: dict[str, Any]) -> tuple[str, int, int]:
        info = request.get("device_info") or {}
        platform = str(info.get("platform") or "")
        # SDK adapters report "browser"; the engine's platform vocabulary
        # says "chrome" (same mapActPlatform normalization the server did).
        if platform == "browser":
            platform = actions.PLATFORM_CHROME
        width = info.get("width")
        height = info.get("height")
        return (
            platform,
            int(width) if isinstance(width, (int, float)) else 0,
            int(height) if isinstance(height, (int, float)) else 0,
        )

    # -- ai loop -------------------------------------------------------

    def _handle_ai(
        self, screenshot: bytes, request: dict[str, Any], params: dict[str, Any]
    ) -> dict[str, Any]:
        platform, dev_w, dev_h = self._device_info(request)
        model_cfg = self._model_config(request)
        tool_params_present = "custom_tools" in params or "exclude_tools" in params
        knowledge_present = "knowledge" in params

        sess = self._session
        is_first_step = sess is None
        if sess is None:
            if not screenshot:
                return _error_payload("screenshot required for first request", False)
            instruction = params.get("instruction")
            if not isinstance(instruction, str) or not instruction:
                return _error_payload(
                    "ai action requires instruction parameter on first request", False
                )
            max_steps = 20
            raw_max = params.get("max_steps")
            if isinstance(raw_max, (int, float)) and raw_max > 0:
                max_steps = int(raw_max)

            custom_tools: list[CustomToolDef] = []
            exclude_tools: list[str] = []
            knowledge = ""
            try:
                if "custom_tools" in params:
                    custom_tools = parse_custom_tools(params["custom_tools"])
                if "exclude_tools" in params:
                    exclude_tools = parse_exclude_tools(params["exclude_tools"], platform)
                if "knowledge" in params:
                    knowledge = parse_knowledge(params["knowledge"])
            except ValueError as exc:
                return _error_payload(str(exc), False)

            sess = LocalAISession(
                instruction=instruction,
                platform=platform,
                language=self._language(request),
                max_steps=max_steps,
                model_config=model_cfg,
                knowledge=knowledge,
                custom_tools=custom_tools,
                exclude_tools=exclude_tools,
                annotate_for_model=self._annotate_for_model,
            )
            self._session = sess

        # Fall back to the last cached screenshot when the SDK omits it
        # (save_note continuations: device state didn't change).
        if not screenshot:
            if not sess.screenshots:
                return _error_payload(
                    "no screenshot uploaded and no cached screenshot available", False
                )
            screenshot = sess.screenshots[-1]

        if sess.step_count >= sess.max_steps:
            # Defensive guard; the normal flow finalizes max-steps on the last
            # allowed step below.
            self._session = None
            return _error_payload(f"max steps reached ({sess.max_steps})", True)

        action_result = request.get("action_result")
        try:
            outcome = sess.step(
                self._engine,
                screenshot,
                action_result if isinstance(action_result, str) else "",
                dev_w,
                dev_h,
                is_first_step,
            )
        except StepError as exc:
            resp = _error_payload(str(exc), exc.finished)
            usage = getattr(exc, "usage", None)
            if isinstance(usage, TokenUsage):
                _put_usage(resp, usage)
            return resp

        resp = self._step_response(outcome)
        _attach_registration_echo(
            resp, sess, tool_params_present, knowledge_present, is_first_step
        )

        if outcome.finished or sess.step_count >= sess.max_steps:
            # done, or the last allowed step ran without finishing: the step
            # itself succeeded — the SDK loop enforces its own max-steps.
            self._session = None
        else:
            sess.remember_screenshot(screenshot)
        return resp

    def _step_response(self, outcome: StepOutcome) -> dict[str, Any]:
        resp: dict[str, Any] = {
            "actionType": outcome.action_type,
            "params": outcome.params,
            "success": True,
            "finished": outcome.finished,
            "status": "succeeded",
            "stepNumber": outcome.step_number,
            "modelAlias": self.model_label,
            "stepDurationMs": outcome.step_duration_ms,
            "llmDecisionDurationMs": outcome.llm_decision_ms,
            "coordinateParseDurationMs": outcome.coordinate_parse_ms,
        }
        if outcome.decision:
            resp["decision"] = outcome.decision
        if outcome.output:
            resp["output"] = outcome.output
        _put_usage(resp, outcome.token_usage)
        return resp

    # -- single-step actions --------------------------------------------

    def _handle_locate(
        self,
        screenshot: bytes,
        request: dict[str, Any],
        action_type: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        locate = params.get("locate")
        if not isinstance(locate, str) or not locate:
            return _error_payload("locate parameter required", False)

        start = time.monotonic()
        usage = TokenUsage()
        llm_ms = 0
        model_used = ""
        result: LocateResult | None = None
        error: Exception | None = None

        for attempt in range(1, 3):
            error = None
            partial: LocateResult | None = None
            try:
                partial = self._engine.locate(
                    LocateInput(
                        locate=locate,
                        screenshot=screenshot,
                        language=self._language(request),
                        model_config=self._model_config(request),
                    )
                )
                result = partial
            except UnsupportedScreenshotError:
                # Deterministic, zero spend — never retried, never billed.
                return _error_payload("screenshot format not supported for locate", False)
            except LocateUnparsableError as exc:
                error = exc
                partial = exc.result
            except ProviderError as exc:
                error = exc
                # Timeouts are not retried: a shot that burned the locate
                # budget won't fit a second attempt in the SDK's step budget.
                if exc.category.value == "timeout":
                    break

            if partial is not None:
                # Accumulate across attempts: a failed first attempt's spend
                # is just as real as the successful second one.
                usage.add(partial.token_usage)
                llm_ms += partial.duration_ms
                model_used = partial.model_used or model_used
            if error is None:
                break
            if attempt == 1:
                logger.warning("vlm locate failed; retrying once: %s", error)

        coord_ms = int((time.monotonic() - start) * 1000)

        def with_stats(resp: dict[str, Any]) -> dict[str, Any]:
            _put_usage(resp, usage)
            resp["llmDecisionDurationMs"] = llm_ms
            resp["coordinateParseDurationMs"] = coord_ms
            if model_used:
                resp["modelAlias"] = self.model_label
            return resp

        if error is not None:
            return with_stats(_error_payload(str(error), False))

        assert result is not None
        if not result.found:
            msg = result.not_found_reason or f"element not found: {locate}"
            return with_stats(_error_payload(msg, False))

        out_params = dict(params)
        out_params["x"] = result.x
        out_params["y"] = result.y
        return with_stats(
            {
                "actionType": action_type,
                "params": out_params,
                "success": True,
                "finished": True,
                "status": "succeeded",
            }
        )

    def _handle_extract(
        self, screenshot: bytes, request: dict[str, Any], params: dict[str, Any]
    ) -> dict[str, Any]:
        prompt = params.get("instruction")
        if not isinstance(prompt, str) or not prompt:
            return _error_payload("extract requires instruction parameter", False)
        try:
            result = self._engine.extract(
                ExtractInput(
                    prompt=prompt,
                    screenshot=screenshot,
                    platform=self._device_info(request)[0],
                    language=self._language(request),
                    model_config=self._model_config(request),
                )
            )
        except ValueError as exc:
            return _error_payload(f"AI extract failed: {exc}", False)

        resp: dict[str, Any] = {
            "success": True,
            "finished": True,
            "status": "succeeded",
            "modelAlias": self.model_label,
            "llmDecisionDurationMs": result.duration_ms,
        }
        if result.result:
            resp["output"] = result.result
        _put_usage(resp, result.token_usage)
        return resp

    def _handle_condition(
        self, screenshot: bytes, request: dict[str, Any], params: dict[str, Any]
    ) -> dict[str, Any]:
        condition = params.get("condition")
        if not isinstance(condition, str) or not condition:
            condition = params.get("assertion")
        if not isinstance(condition, str) or not condition:
            return _error_payload("condition/assertion parameter required", False)
        try:
            result = self._engine.check_condition(
                ConditionInput(
                    condition=condition,
                    screenshot=screenshot,
                    platform=self._device_info(request)[0],
                    language=self._language(request),
                    model_config=self._model_config(request),
                )
            )
        except ValueError as exc:
            return _error_payload(f"AI condition check failed: {exc}", False)

        # success is always true (the check ran); finished carries the
        # verdict — an unmet condition is a valid result, not a failure.
        # wait_for's SDK-side polling depends on exactly this contract.
        resp: dict[str, Any] = {
            "success": True,
            "finished": result.met,
            "status": "succeeded",
            "decision": "condition met" if result.met else "condition not met",
            "modelAlias": self.model_label,
            "llmDecisionDurationMs": result.duration_ms,
        }
        if result.reasoning:
            resp["output"] = result.reasoning
        _put_usage(resp, result.token_usage)
        return resp


def _attach_registration_echo(
    resp: dict[str, Any],
    sess: LocalAISession,
    tool_params_present: bool,
    knowledge_present: bool,
    is_first_step: bool,
) -> None:
    """Echo the effective tool/knowledge registration whenever the request
    carried the params — the SDK keys its support detection on the fields'
    presence, and warns on their absence."""
    warnings: list[str] = []
    if tool_params_present:
        resp["tool_registration"] = {
            "registered": [d.name for d in sess.custom_tools],
            "excluded": list(sess.exclude_tools),
        }
        if not is_first_step:
            warnings.append(
                "custom_tools/exclude_tools already registered for this session; "
                "incoming values ignored"
            )
    if knowledge_present:
        resp["knowledge_registered"] = len(sess.knowledge.encode("utf-8"))
        if not is_first_step:
            warnings.append(
                "knowledge already registered for this session; incoming value ignored"
            )
    if warnings:
        resp["warning"] = "; ".join(warnings)


def _error_payload(message: str, finished: bool) -> dict[str, Any]:
    return {"success": False, "finished": finished, "error": message, "status": "failed"}


def _put_usage(resp: dict[str, Any], usage: TokenUsage) -> None:
    if usage.input_tokens:
        resp["inputTokens"] = usage.input_tokens
    if usage.output_tokens:
        resp["outputTokens"] = usage.output_tokens
    if usage.thinking_tokens:
        resp["thinkingTokens"] = usage.thinking_tokens
    if usage.cache_read_tokens:
        resp["cacheReadTokens"] = usage.cache_read_tokens
    if usage.cache_write_tokens:
        resp["cacheWriteTokens"] = usage.cache_write_tokens


class _Tracer:
    """QIRA_ENGINE_TRACE=<dir>: append one JSONL record per act() call
    (request/response sans screenshot bytes; the screenshot lands next to the
    log under its sha256). Debug aid and regression corpus — never on by
    default."""

    def __init__(self, directory: str) -> None:
        self._dir = directory

    @classmethod
    def from_env(cls) -> "_Tracer | None":
        d = os.environ.get("QIRA_ENGINE_TRACE", "").strip()
        if not d:
            return None
        os.makedirs(d, exist_ok=True)
        return cls(d)

    def record(
        self,
        action_type: str,
        screenshot: bytes,
        request: dict[str, Any],
        response: dict[str, Any],
    ) -> None:
        try:
            sha = ""
            if screenshot:
                sha = hashlib.sha256(screenshot).hexdigest()
                path = os.path.join(self._dir, f"{sha}.img")
                if not os.path.exists(path):
                    with open(path, "wb") as f:
                        f.write(screenshot)
            entry = {
                "ts": time.time(),
                "action_type": action_type,
                "screenshot_sha256": sha,
                "request": request,
                "response": response,
            }
            with open(os.path.join(self._dir, "trace.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError as exc:  # tracing must never break a step
            logger.warning("engine trace write failed: %s", exc)


# _Tracer.from_env returns None when tracing is off; give act() a no-op shim.
class _NullTracer:
    def record(self, *args: Any, **kwargs: Any) -> None:
        return None
