"""LocalBackend: the in-process decision engine behind the SDK client.

Originally built as a drop-in replacement for the v2 cloud /act endpoint,
speaking that wire protocol's request/response dicts. With the cloud gone the
wire emulation went with it: the client talks to the engine through typed
calls — an explicit :class:`AIRun` per ai() command, one method per
single-step action. Session lifetime belongs to the caller's loop (a local
variable, not backend state), so an abandoned run can never leak into the
next one.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
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
    ExtractInput,
    LocateInput,
    LocateResult,
    LocateUnparsableError,
    ModelConfig,
    TokenUsage,
    UnsupportedScreenshotError,
)

logger = logging.getLogger("qirabot.engine")


@dataclass
class LocateOutcome:
    """One element-location call (click/type_text/…/locate). A model-level
    failure (element not found, unparsable after retry, provider error) is a
    result, not an exception — its spend is just as real and the caller folds
    ``token_usage`` into the session totals either way."""

    found: bool
    x: int = 0
    y: int = 0
    error: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    llm_ms: int = 0
    coord_ms: int = 0


@dataclass
class ExtractOutcome:
    """One extract call."""

    result: str
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    llm_ms: int = 0


@dataclass
class ConditionOutcome:
    """One assert/wait_for check. ``met`` carries the verdict — an unmet
    condition is a valid result, not a failure; wait_for's SDK-side polling
    depends on exactly this contract."""

    met: bool
    reasoning: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    llm_ms: int = 0


def _normalize_platform(platform: str) -> str:
    # SDK adapters report "browser"; the engine's platform vocabulary says
    # "chrome" (same mapActPlatform normalization the v2 server did).
    return actions.PLATFORM_CHROME if platform == "browser" else platform


class AIRun:
    """One in-flight ai() command, created by :meth:`LocalBackend.start_ai`.

    The caller owns the lifetime: hold it for the duration of the loop and
    drop it when the loop ends (finished, max steps, error, or abort). There
    is no backend-side slot to reset — a run abandoned mid-way is garbage
    collected with the loop frame and cannot hijack the next run."""

    def __init__(self, backend: "LocalBackend", session: LocalAISession) -> None:
        self._backend = backend
        self._session = session

    def step(
        self,
        screenshot: bytes,
        action_result: str = "",
        device_width: int = 0,
        device_height: int = 0,
    ) -> StepOutcome:
        """Run one decide→ground step. Raises :class:`StepError` (carrying
        the failed attempts' token usage) for step-level failures and
        :class:`ProviderError` for transport-level ones."""
        if not screenshot:
            raise StepError("screenshot required")
        sess = self._session
        is_first_step = sess.step_count == 0
        trace_req = {
            "action_result": action_result,
            "device": [device_width, device_height],
        }
        if is_first_step:
            trace_req["instruction"] = sess.instruction
        try:
            outcome = sess.step(
                self._backend._engine,
                screenshot,
                action_result,
                device_width,
                device_height,
                is_first_step,
            )
        except (StepError, ProviderError) as exc:
            self._backend._trace.record("ai", screenshot, trace_req, {"error": str(exc)})
            raise
        if not outcome.finished:
            # History replay needs one screenshot per committed step (save_note
            # steps re-send the previous frame; the duplicate keeps alignment).
            sess.remember_screenshot(screenshot)
        self._backend._trace.record("ai", screenshot, trace_req, _outcome_trace(outcome))
        return outcome


class LocalBackend:
    """One backend per Qirabot instance. Stateless between calls apart from
    the provider connection: ai() session state lives in the :class:`AIRun`
    handed to the caller."""

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
        if self._http is not None:
            self._http.close()
            self._http = None

    # -- model config --------------------------------------------------

    def _model_config(self, thinking_level: str = "") -> ModelConfig:
        # Engine-level defaults, mirroring what the v2 cloud aliases always
        # sent. Without them the provider ports' zero-value fallbacks apply:
        # temperature 0.0 (Gemini 3 degrades below its recommended 1.0) and
        # the API-side media resolution. Screenshots default to "high" — UI
        # text is dense and the decision quality is the product.
        # thinking_level: per-call override > constructor > "low". The
        # explicit "low" floor matches every v2 cloud alias (fast ran
        # minimal); leaving it unset would drift with the API-side default,
        # which for Gemini 3 is high — slower and pricier on every step.
        params: dict[str, Any] = {
            "temperature": 1.0,
            "media_resolution": self._media_resolution or "high",
            "thinking_level": thinking_level or self._thinking_level or "low",
        }
        return ModelConfig(
            provider=self._spec.provider,
            model=self._spec.model,
            params=params,
            locate_format=self._locate_format,
        )

    # -- ai ---------------------------------------------------------------

    def start_ai(
        self,
        instruction: str,
        *,
        platform: str,
        max_steps: int = 20,
        language: str = "",
        thinking_level: str = "",
        custom_tools: list[dict[str, Any]] | None = None,
        exclude_tools: list[str] | None = None,
        knowledge: str = "",
    ) -> AIRun:
        """Create the session for one ai() command and hand it to the caller.

        ``custom_tools`` takes the SDK's wire-shaped definitions (name/
        description/parameters dicts); validation lives here so the engine
        enforces the same rules no matter who builds them. Raises ValueError
        for invalid instruction/tools/knowledge."""
        if not isinstance(instruction, str) or not instruction:
            raise ValueError("ai requires a non-empty instruction")
        platform = _normalize_platform(platform)
        tools = parse_custom_tools(custom_tools) if custom_tools else []
        excludes = parse_exclude_tools(exclude_tools, platform) if exclude_tools else []
        knowledge_text = parse_knowledge(knowledge) if knowledge else ""
        if tools or excludes:
            logger.info(
                "custom tools registered: %s; excluded: %s",
                [d.name for d in tools],
                excludes,
            )
        if knowledge_text:
            logger.info(
                "knowledge registered: %d bytes", len(knowledge_text.encode("utf-8"))
            )
        session = LocalAISession(
            instruction=instruction,
            platform=platform,
            language=language,
            max_steps=max_steps if max_steps > 0 else 20,
            model_config=self._model_config(thinking_level),
            knowledge=knowledge_text,
            custom_tools=tools,
            exclude_tools=excludes,
            annotate_for_model=self._annotate_for_model,
        )
        return AIRun(self, session)

    # -- single-step actions --------------------------------------------

    def locate(
        self,
        screenshot: bytes,
        locate: str,
        *,
        language: str = "",
        thinking_level: str = "",
    ) -> LocateOutcome:
        """Resolve an element description to device coordinates with one VLM
        call, retrying once on an unparsable response. Raises ValueError only
        for a missing description; every model-level failure comes back as a
        not-found outcome carrying the real spend."""
        if not isinstance(locate, str) or not locate:
            raise ValueError("locate parameter required")

        start = time.monotonic()
        usage = TokenUsage()
        llm_ms = 0
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
                        language=language,
                        model_config=self._model_config(thinking_level),
                    )
                )
                result = partial
            except UnsupportedScreenshotError:
                # Deterministic, zero spend — never retried, never billed.
                return self._locate_done(
                    screenshot,
                    locate,
                    LocateOutcome(
                        found=False, error="screenshot format not supported for locate"
                    ),
                )
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
            if error is None:
                break
            if attempt == 1:
                logger.warning("vlm locate failed; retrying once: %s", error)

        coord_ms = int((time.monotonic() - start) * 1000)

        if error is not None:
            outcome = LocateOutcome(
                found=False,
                error=str(error),
                token_usage=usage,
                llm_ms=llm_ms,
                coord_ms=coord_ms,
            )
        else:
            assert result is not None
            if result.found:
                outcome = LocateOutcome(
                    found=True,
                    x=result.x,
                    y=result.y,
                    token_usage=usage,
                    llm_ms=llm_ms,
                    coord_ms=coord_ms,
                )
            else:
                outcome = LocateOutcome(
                    found=False,
                    error=result.not_found_reason or f"element not found: {locate}",
                    token_usage=usage,
                    llm_ms=llm_ms,
                    coord_ms=coord_ms,
                )
        return self._locate_done(screenshot, locate, outcome)

    def _locate_done(
        self, screenshot: bytes, locate: str, outcome: LocateOutcome
    ) -> LocateOutcome:
        self._trace.record(
            "locate",
            screenshot,
            {"locate": locate},
            {
                "found": outcome.found,
                "x": outcome.x,
                "y": outcome.y,
                "error": outcome.error,
                "token_usage": vars(outcome.token_usage),
            },
        )
        return outcome

    def extract(
        self,
        screenshot: bytes,
        instruction: str,
        *,
        platform: str = "",
        language: str = "",
        thinking_level: str = "",
    ) -> ExtractOutcome:
        """One extract call. Raises ValueError (engine-level failure) or
        ProviderError; neither carries usage — parity with the v2 engine,
        which never metered these failure paths."""
        if not isinstance(instruction, str) or not instruction:
            raise ValueError("extract requires instruction parameter")
        try:
            result = self._engine.extract(
                ExtractInput(
                    prompt=instruction,
                    screenshot=screenshot,
                    platform=_normalize_platform(platform),
                    language=language,
                    model_config=self._model_config(thinking_level),
                )
            )
        except (ValueError, ProviderError) as exc:
            self._trace.record(
                "extract", screenshot, {"instruction": instruction}, {"error": str(exc)}
            )
            raise
        self._trace.record(
            "extract",
            screenshot,
            {"instruction": instruction},
            {"result": result.result, "token_usage": vars(result.token_usage)},
        )
        return ExtractOutcome(
            result=result.result,
            token_usage=result.token_usage,
            llm_ms=result.duration_ms,
        )

    def check_condition(
        self,
        screenshot: bytes,
        condition: str,
        *,
        platform: str = "",
        language: str = "",
        thinking_level: str = "",
    ) -> ConditionOutcome:
        """One assert/wait_for check. Raises like :meth:`extract`."""
        if not isinstance(condition, str) or not condition:
            raise ValueError("condition/assertion parameter required")
        try:
            result = self._engine.check_condition(
                ConditionInput(
                    condition=condition,
                    screenshot=screenshot,
                    platform=_normalize_platform(platform),
                    language=language,
                    model_config=self._model_config(thinking_level),
                )
            )
        except (ValueError, ProviderError) as exc:
            self._trace.record(
                "condition", screenshot, {"condition": condition}, {"error": str(exc)}
            )
            raise
        self._trace.record(
            "condition",
            screenshot,
            {"condition": condition},
            {
                "met": result.met,
                "reasoning": result.reasoning,
                "token_usage": vars(result.token_usage),
            },
        )
        return ConditionOutcome(
            met=result.met,
            reasoning=result.reasoning,
            token_usage=result.token_usage,
            llm_ms=result.duration_ms,
        )


def _outcome_trace(outcome: StepOutcome) -> dict[str, Any]:
    return {
        "action_type": outcome.action_type,
        "params": outcome.params,
        "decision": outcome.decision,
        "output": outcome.output,
        "finished": outcome.finished,
        "step_number": outcome.step_number,
        "token_usage": vars(outcome.token_usage),
    }


class _Tracer:
    """QIRA_ENGINE_TRACE=<dir>: append one JSONL record per engine call
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


# _Tracer.from_env returns None when tracing is off; give callers a no-op shim.
class _NullTracer:
    def record(self, *args: Any, **kwargs: Any) -> None:
        return None
