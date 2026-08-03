"""Qirabot SDK client."""

from __future__ import annotations

import atexit
import contextlib
import io
import logging
import os
import signal
import threading
import time
import uuid
import weakref
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from qirabot._browser import launch_browser
from qirabot._knowledge import resolve_knowledge
from qirabot._tools import build_tool_defs
from qirabot.engine.local_backend import LocalBackend
from qirabot.engine.providers.registry import resolve_default_model
from qirabot.recording import MjpegStreamRecorder, Recorder, ScreenRecorder, device_recorder
from qirabot.adapters import auto
from qirabot.adapters.base import DeviceAdapter, ScreenshotConfig
from qirabot.bound import _BoundQirabot
from qirabot.exceptions import (
    ActionError,
    AuthenticationError,
    QirabotError,
    QirabotTimeoutError,
    _is_retryable,
)
from qirabot.overlay import Overlay

logger = logging.getLogger("qirabot")


def _stale_key_source() -> str:
    """Where the leftover v2 QIRA_API_KEY came from, for error messages.

    The CLI loads ``./.env`` into the environment before the SDK ever runs, so
    by the time the migration guard fires the variable's origin is invisible —
    except that :mod:`qirabot._dotenv` records which keys it injected. Name
    the file when we know it; otherwise point at the places a real export
    would live.
    """
    from qirabot._dotenv import injected_from

    path = injected_from("QIRA_API_KEY")
    if path:
        return f"(loaded from {path})"
    return "(exported in your environment — check your shell profile or CI config)"


@contextlib.contextmanager
def _suppress_sigint() -> Iterator[None]:
    """Make the wrapped block uninterruptible by Ctrl+C (SIGINT).

    Used in :meth:`Qirabot.close` so a flurry of Ctrl+C during shutdown cannot
    skip writing the run report — a plain try/except can't guarantee this because
    Python delivers each SIGINT as a fresh ``KeyboardInterrupt`` at whatever
    bytecode boundary it lands on, including inside the report write itself.

    Only the SIGINTs that arrive *inside* the block are suppressed; the original
    KeyboardInterrupt that triggered shutdown keeps propagating once we return.
    A no-op (best-effort) off the main thread or where SIGINT can't be reassigned
    (``signal.signal`` is main-thread only) — callers keep their own try/except
    as the fallback for that case.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    try:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except (ValueError, OSError):
        yield
        return
    try:
        yield
    finally:
        try:
            signal.signal(signal.SIGINT, previous)
        except (ValueError, OSError):
            pass


@dataclass
class StepResult:
    """Result of a single step in bot.ai()."""

    step: int
    action_type: str
    params: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    finished: bool = False
    decision: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    step_duration_ms: int = 0
    llm_decision_duration_ms: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any], step: int) -> StepResult:
        return cls(
            step=step,
            action_type=data.get("actionType", ""),
            params=data.get("params") or {},
            output=data.get("output", ""),
            finished=data.get("finished", False),
            decision=data.get("decision", ""),
            input_tokens=data.get("inputTokens", 0),
            output_tokens=data.get("outputTokens", 0),
            thinking_tokens=data.get("thinkingTokens", 0),
            cache_read_tokens=data.get("cacheReadTokens", 0),
            cache_write_tokens=data.get("cacheWriteTokens", 0),
            step_duration_ms=data.get("stepDurationMs", 0),
            llm_decision_duration_ms=data.get("llmDecisionDurationMs", 0),
        )


@dataclass
class VerifyResult:
    """Result of bot.verify(). Truthy when the assertion holds.

    Use directly as a bool (``if bot.verify(...)`` / ``assert bot.verify(...)``);
    read ``reason`` for the model's explanation, e.g. when an assertion fails
    unexpectedly. ``output_tokens`` already includes ``thinking_tokens``
    (Anthropic semantics), so this call's spend is ``input_tokens +
    output_tokens`` — do not add thinking again.
    """

    passed: bool
    reason: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VerifyResult:
        return cls(
            passed=data.get("finished", False),
            reason=data.get("output", ""),
            input_tokens=data.get("inputTokens", 0),
            output_tokens=data.get("outputTokens", 0),
            thinking_tokens=data.get("thinkingTokens", 0),
        )

    def __bool__(self) -> bool:
        return self.passed


class ExtractResult(str):
    """Text extracted by bot.extract(); usable directly as a str.

    Behaves as the extracted string for every str operation and additionally
    carries the extraction's token usage. ``output_tokens`` already includes
    ``thinking_tokens`` (Anthropic semantics): this call's spend is
    ``input_tokens + output_tokens``. Note: str operations that build a new
    string (slicing, concatenation, ``.strip()``) return a plain str and drop
    these attributes — read tokens on the value returned by extract() itself.
    """

    input_tokens: int
    output_tokens: int
    thinking_tokens: int

    def __new__(
        cls,
        text: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        thinking_tokens: int = 0,
    ) -> ExtractResult:
        obj = super().__new__(cls, text)
        obj.input_tokens = input_tokens
        obj.output_tokens = output_tokens
        obj.thinking_tokens = thinking_tokens
        return obj

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExtractResult:
        return cls(
            data.get("output", ""),
            input_tokens=data.get("inputTokens", 0),
            output_tokens=data.get("outputTokens", 0),
            thinking_tokens=data.get("thinkingTokens", 0),
        )


@dataclass
class LocateResult:
    """Result of bot.locate(): the resolved element coordinates.

    ``x``/``y`` are in the adapter's screenshot pixel space — window-relative
    client pixels on the Windows window backend, physical screen pixels on
    pyautogui, device pixels on mobile backends. They match what you see in
    the report screenshots and what the same adapter's own actions use, but
    are not necessarily OS-global coordinates.

    Supports tuple unpacking: ``x, y = bot.locate(...)``.

    WARNING: the vision resolver returns coordinates even when the element is
    absent from the screen — such coordinates are meaningless. Gate with
    ``timeout=`` (auto-wait) or :meth:`Qirabot.verify`/:meth:`Qirabot.wait_for`
    when presence is not guaranteed.
    """

    x: int
    y: int
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0

    def __iter__(self) -> Iterator[int]:
        yield self.x
        yield self.y

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LocateResult:
        params = data.get("params") or {}
        return cls(
            x=int(round(float(params.get("x", 0)))),
            y=int(round(float(params.get("y", 0)))),
            input_tokens=data.get("inputTokens", 0),
            output_tokens=data.get("outputTokens", 0),
            thinking_tokens=data.get("thinkingTokens", 0),
        )


# How a bot.ai() run ended. "max_steps" matches the server's task-level
# max_steps event name; the server itself records the command as plain
# "failed", so this is the SDK's finer-grained local view.
RunStatus = Literal["completed", "goal_failed", "max_steps", "error"]


@dataclass
class RunResult:
    """Result of bot.ai() multi-step operation.

    ``success`` is the pass/fail verdict (True only when the model declared the
    goal achieved); ``status`` says *how* the run ended:

    - ``"completed"``: model declared done and the goal was achieved
    - ``"goal_failed"``: model concluded the goal is unreachable (login wall,
      captcha, frozen app)
    - ``"max_steps"``: step budget ran out before the model finished — a
      truncation, not a capability verdict; consider raising ``max_steps``
    - ``"error"``: the server reported a terminal error

    ``success`` is True iff ``status == "completed"``.
    """

    success: bool
    output: str = ""
    steps: list[StepResult] = field(default_factory=list)
    status: RunStatus = "completed"


@dataclass(frozen=True)
class SessionUsage:
    """Session-wide AI usage totals, read via :attr:`Qirabot.usage`.

    Accumulates over every AI call on the client — ai() steps, AI-located
    actions (click()/type_text()/…) and standalone
    verify()/extract()/locate() calls — including the spend of failed calls
    and cancelled runs. ``ai_steps`` counts successful calls only; a failed
    call's tokens still land in the totals. A frozen snapshot: read it again
    for updated totals.

    Token semantics: ``input_tokens`` is the non-cached prompt tokens only
    (both providers); the cached prompt portion rides in
    ``cache_read_tokens``/``cache_write_tokens``. ``output_tokens`` already
    includes ``thinking_tokens`` (Anthropic semantics; the Gemini provider
    normalizes to match), so :attr:`total_tokens` is
    input + cache read/write + output — thinking is not added again.
    """

    ai_steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    step_duration_ms: int = 0
    llm_decision_duration_ms: int = 0

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.output_tokens
        )


class Qirabot:
    """AI automation bolt-on for any framework.

    The decision engine runs locally (v3.0+): screenshots go straight to the
    model you configure via Vertex AI with your own GCP credentials — no
    Qirabot server is involved. Authentication is ADC by default; for
    gemini-vertex models a Vertex AI API key works instead
    (``vertex_api_key=`` / ``QIRA_VERTEX_API_KEY``) — no gcloud setup, but
    global endpoint only and Google models only. The ``gemini`` provider
    calls the Gemini Developer API instead of Vertex and takes an AI Studio
    key (``gemini_api_key=`` / ``QIRA_GEMINI_API_KEY`` / ``GEMINI_API_KEY``).

    Usage::

        bot = Qirabot(model="gemini-vertex/gemini-3.6-flash")
        bot.click(page, "Login button")
        bot.type_text(page, "Username field", "admin@example.com")
        result = bot.ai(page, "Find the cheapest item and add to cart")
        bot.close()
    """

    def __init__(
        self,
        model: str = "",
        vertex_project: str = "",
        vertex_location: str = "",
        vertex_api_key: str = "",
        gemini_api_key: str = "",
        thinking_level: str = "",
        media_resolution: str = "",
        language: str = "",
        task_name: str = "",
        locate_format: str = "",
        report: bool = True,
        report_dir: str = "",
        screenshot_format: str = "jpeg",
        screenshot_quality: int = 80,
        screenshot_annotate: bool = True,
        retry: int = 1,
        retry_delay: float = 1.0,
        settle_seconds: float | None = None,
        record: bool = False,
        record_fps: int = 12,
        record_window: bool = False,
        record_audio: bool | str = False,
        record_audio_offset: float | None = None,
        record_mjpeg_url: str | None = None,
        record_device: bool = False,
        overlay: bool = False,
    ):
        # v2 configured a Qirabot cloud API key; v3 runs the decision engine
        # locally against your own Vertex AI credentials. A leftover
        # QIRA_API_KEY with no explicit model choice means the user has not
        # acknowledged the v3 switch — refuse with a migration pointer rather
        # than silently changing which account gets billed. Passing model= or
        # setting QIRA_MODEL counts as opting in and disarms the guard.
        stale_key = bool(os.environ.get("QIRA_API_KEY")) and not (
            model or os.environ.get("QIRA_MODEL", "").strip()
        )
        # ADC + project resolution and the provider handshake happen here so a
        # bad credential setup fails at construction, not mid-run. Config
        # failures surface as the SDK's own exception types: missing/broken
        # credentials -> AuthenticationError, bad model/project values ->
        # QirabotError (the engine's messages are already actionable). The
        # stale-key guard deliberately fires *after* the handshake: knowing
        # whether ADC works lets the message name the user's actual next step
        # instead of prescribing credential setup they may already have.
        from qirabot.engine.providers.base import ProviderError

        try:
            self._backend = LocalBackend(
                model=model or resolve_default_model(),
                vertex_project=vertex_project,
                vertex_location=vertex_location,
                vertex_api_key=vertex_api_key,
                gemini_api_key=gemini_api_key,
                thinking_level=thinking_level,
                media_resolution=media_resolution
                or os.environ.get("QIRA_MEDIA_RESOLUTION", ""),
                locate_format=locate_format or os.environ.get("QIRA_LOCATE_FORMAT", ""),
            )
        except ProviderError as e:
            if stale_key:
                raise AuthenticationError(
                    "Qirabot v3 runs the decision engine locally — the cloud "
                    "backend is gone, but a v2-era QIRA_API_KEY is still set "
                    f"{_stale_key_source()}. To use v3: remove QIRA_API_KEY "
                    "(and QIRA_BASE_URL), configure Google Cloud ADC (set "
                    "GOOGLE_APPLICATION_CREDENTIALS or run `gcloud auth "
                    "application-default login`), and optionally pick a model "
                    'via model=... / QIRA_MODEL (e.g. "gemini-vertex/'
                    'gemini-3.6-flash"). To keep the old cloud behavior, '
                    f"pin qirabot<3. (credential check: {e})",
                    code="auth.cloud_removed",
                ) from e
            raise AuthenticationError(str(e), code="auth.credentials") from e
        except ValueError as e:
            raise QirabotError(str(e), code="config.invalid") from e
        if stale_key:
            self._backend.close()
            raise AuthenticationError(
                "Your Google Cloud setup works and Qirabot v3 is ready to use "
                "it — but a v2-era QIRA_API_KEY is still set "
                f"{_stale_key_source()}, and the cloud backend it belonged to "
                "is gone. Refusing to start rather than silently switching "
                "billing to your GCP project: remove QIRA_API_KEY (and "
                "QIRA_BASE_URL), or opt into v3 explicitly via model=... / "
                "QIRA_MODEL (e.g. "
                '"gemini-vertex/gemini-3.6-flash"). To keep the old '
                "cloud behavior, pin qirabot<3.",
                code="auth.cloud_removed",
            )
        self._adapters: dict[int, DeviceAdapter] = {}
        self._pw_instances: list[Any] = []
        self._cdp_pages: list[Any] = []
        # Instance-wide thinking override, sent with every step. "" = the
        # engine default. Granularity depends on the underlying model.
        self._thinking_level = thinking_level
        self._language = language
        self._task_name = task_name
        self._closed = False
        # On-screen progress window (capture-excluded, click-through); a no-op
        # on unsupported platforms, so gating here is on intent alone.
        self._overlay: Overlay | None = Overlay() if overlay else None
        # Set once a terminal outcome was recorded (fail()/cancel()), so
        # close()'s default bookkeeping never overrides an explicit failure.
        self._terminalized = False
        # Local run id: everything cloud-side is gone, but the report
        # directory naming and report header still key off a task id.
        self._task_id: str | None = f"local-{uuid.uuid4().hex[:8]}"
        # Per-run output directory, bucketed by date to avoid one flat pile:
        #   <root>/<YYYY-MM-DD>/<HHMMSS>-<task_id[:8]>/
        # report_dir / QIRA_REPORT_DIR set only the root; the date/run subdirs
        # are added automatically so one env var works across many runs.
        self._report = report
        root = report_dir or os.environ.get("QIRA_REPORT_DIR", "") or "./qira_runs"
        short = (self._task_id or "run")[:8]
        self._report_dir = (
            Path(root).expanduser()
            / time.strftime("%Y-%m-%d")
            / f"{time.strftime('%H%M%S')}-{short}"
        )
        # Session-wide action timeline for the report, and the current task
        # section ai() runs are grouped under. Standalone actions carry the
        # "setup" key, which the report renders as "manual".
        self._log: list[dict[str, Any]] = []
        self._current_section = "setup"
        # instruction -> times ai() has run it, to give repeat runs a
        # numbered section key ("<instruction> #2") so each run keeps its own
        # outcome/error instead of the later run overwriting the earlier.
        self._section_runs: dict[str, int] = {}
        # ai() section key -> RunStatus, for the per-section badge in the
        # report (completed / goal_failed / max_steps / error).
        self._section_outcomes: dict[str, str] = {}
        # ai() instruction -> failure text, rendered as a banner above the
        # section's step table (max-steps truncation / server terminal error).
        # These used to be synthetic step entries in _log, which made the
        # report's step count disagree with the server's.
        self._section_errors: dict[str, str] = {}
        # Outcome of the most recent ai() call, driving close()'s auto-complete
        # status: a run whose last command errored must not be recorded as
        # "completed" just because close() ran (atexit after a crash included).
        # goal_failed/max_steps stay "completed" at task level — the command
        # ran; whether that fails the task is the script's call via fail().
        self._last_ai_status: str | None = None
        # Latched by a user abort (ESC hold / mouse-to-corner): blocks every
        # later ai() on this client until clear_user_abort(). ESC means
        # "give me the machine back" — its scope is the script's autonomous
        # control as a whole, not just the ai() call that happened to be
        # running; a try/except around bot.ai() must not re-take control.
        self._user_aborted = False
        self._last_ai_error = ""
        # Session-wide totals for the report header. Token/timing data rides in
        # each ai() step result, not in _log, so we accumulate it here as steps
        # run and hand the totals to the report at render time.
        self._stats: dict[str, int] = {
            "ai_steps": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "step_duration_ms": 0,
            "llm_decision_duration_ms": 0,
        }
        self._screenshot_counter = 0
        self._screenshot_config = ScreenshotConfig(
            format=screenshot_format,
            quality=screenshot_quality,
            annotate=screenshot_annotate,
        )
        self._retry = retry
        self._retry_delay = retry_delay
        # Fixed delay (seconds) each adapter sleeps after a screen-changing action
        # so the next screenshot lands on the repainted frame. ``None`` keeps each
        # platform's built-in default (desktop 1.0 / mobile 0.6 / browser 0.6 /
        # adb 1); an explicit value (incl. 0 to disable) overrides all of them.
        # Falls back to the QIRA_SETTLE_SECONDS env var when the arg is omitted.
        if settle_seconds is None:
            env_settle = os.environ.get("QIRA_SETTLE_SECONDS", "")
            if env_settle:
                try:
                    settle_seconds = float(env_settle)
                except ValueError:
                    raise ValueError(
                        f"QIRA_SETTLE_SECONDS must be a number, got {env_settle!r}"
                    )
        if settle_seconds is not None and settle_seconds < 0:
            raise ValueError(f"settle_seconds must be >= 0, got {settle_seconds}")
        self._settle_seconds = settle_seconds
        # Built-in ffmpeg full-screen recording. Opt-in (default off); the
        # QIRA_RECORD env var enables it without a code change. Auto-started here
        # and stopped in close() so the mp4 is finalized before the report scans
        # for it. A single recorder slot + a fixed output path mean the auto path
        # and the manual start_recording()/stop_recording() never spawn two ffmpegs.
        self._record = record or _env_truthy(os.environ.get("QIRA_RECORD", ""))
        self._record_fps = record_fps
        # Windows-only recording extras: follow the window under test (resolved
        # lazily from the first action's target) and capture system audio.
        self._record_window = record_window or _env_truthy(os.environ.get("QIRA_RECORD_WINDOW", ""))
        # By default record_window crops a desktop grab to the window's visible
        # rect (works for GPU/game windows the per-window path renders black).
        # QIRA_RECORD_WINDOW_NATIVE=1 forces the legacy gdigrab per-window mode,
        # which can follow a background/occluded *non-GPU* window but goes black
        # on games.
        self._record_window_native = _env_truthy(os.environ.get("QIRA_RECORD_WINDOW_NATIVE", ""))
        self._record_audio = record_audio or _env_truthy(os.environ.get("QIRA_RECORD_AUDIO", ""))
        if record_audio_offset is None:
            env_off = os.environ.get("QIRA_AUDIO_OFFSET", "")
            if env_off:
                try:
                    record_audio_offset = float(env_off)
                except ValueError:
                    raise ValueError(f"QIRA_AUDIO_OFFSET must be a number, got {env_off!r}")
        self._record_audio_offset = record_audio_offset
        # Record an MJPEG stream (WDA's device-screen stream, port 9100) instead
        # of the host screen — the only way `record=True` can capture an iOS
        # device's screen rather than the desktop the SDK runs on.
        self._record_mjpeg_url = record_mjpeg_url or os.environ.get("QIRA_RECORD_MJPEG_URL", "") or None
        # Record the automated device's own screen instead of the host screen:
        # the recorder is picked from the first action's target (Appium driver
        # → session recording API; AdbDevice → adb screenrecord), so the
        # start is deferred like record_window's.
        self._record_device = record_device or _env_truthy(os.environ.get("QIRA_RECORD_DEVICE", ""))
        self._recorder: Recorder | None = None
        # Epoch time the current recording started; anchors the report's
        # per-step video-seek offsets. 0.0 = no recording started this run.
        self._record_started_ts = 0.0
        # True while a recording is still owed: claimed (set False) right before a
        # recorder starts, which also guards against re-entrancy through
        # _get_adapter when window-following resolves the target.
        self._record_pending = (
            self._record or self._record_device or bool(self._record_mjpeg_url)
        ) and self._report
        atexit.register(self.close)
        self._maybe_start_recording()

    @property
    def report_dir(self) -> str:
        """The per-run output directory (report.html + screenshots/ + recording).

        Pass ``record=True`` (or set ``QIRA_RECORD=1``) and the SDK records the
        full screen here as ``recording.mp4`` via ffmpeg, embedding it in the
        report automatically; :meth:`start_recording`/:meth:`stop_recording`
        drive it manually. Dropping your own ``recording.mp4`` into this dir is
        also picked up.

        Creating the directory on access keeps the recording/output patterns
        working even when nothing has been written to it yet (e.g. a run that
        crashes on its first action, before any screenshot).
        """
        self._report_dir.mkdir(parents=True, exist_ok=True)
        return str(self._report_dir)

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def usage(self) -> SessionUsage:
        """Session-wide AI usage totals so far (tokens, AI steps, timing).

        Covers every AI call on this client — ai() steps, AI-located actions
        (click()/type_text()/…) and standalone verify()/extract()/locate() —
        including the spend of failed calls and cancelled runs. Returns a
        frozen snapshot; read again for updated totals. See
        :class:`SessionUsage` for the token-counting semantics.
        """
        return SessionUsage(
            ai_steps=self._stats["ai_steps"],
            input_tokens=self._stats["input_tokens"],
            output_tokens=self._stats["output_tokens"],
            thinking_tokens=self._stats["thinking_tokens"],
            cache_read_tokens=self._stats["cache_read_tokens"],
            cache_write_tokens=self._stats["cache_write_tokens"],
            step_duration_ms=self._stats["step_duration_ms"],
            llm_decision_duration_ms=self._stats["llm_decision_duration_ms"],
        )

    def bind(self, target: Any) -> _BoundQirabot:
        """Bind a target once and drop it from subsequent calls.

        Returns a drop-in proxy you use exactly like this ``Qirabot``: action
        methods (``click``/``type_text``/``ai``/…) no longer take ``target`` as
        their first argument, and lifecycle/context-manager methods delegate to
        this instance::

            with Qirabot().bind(driver) as bot:
                bot.click("Login")
                bot.type_text("Email", "a@b.com")

        Best for frameworks that drive a single, stable target for the whole
        session (adb, WDA, Windows, pyautogui, Appium, Selenium). For Playwright's
        new-tab flows the explicit ``page = bot.click(page, ...)`` form keeps
        the returned (possibly new) page visible; with a bound proxy, reach the
        live page via ``bot.current_page()`` for native Playwright interop.
        """
        return _BoundQirabot(self, target)

    def open(
        self,
        url: str = "",
        headless: bool = False,
        *,
        viewport: tuple[int, int] = (1280, 800),
        user_data_dir: str = "",
        channel: str = "",
        args: list[str] | None = None,
        cdp_url: str = "",
    ) -> Any:
        """Launch a browser and optionally navigate to a URL.

        Args:
            url: optional URL to open. If no scheme present, ``https://`` is prepended.
            headless: run without a visible window. On Linux with no display
                server (``DISPLAY``/``WAYLAND_DISPLAY`` both unset) a headed
                launch cannot work, so ``headless=False`` falls back to
                headless with a warning.
            viewport: ``(width, height)`` in pixels. Ignored when ``cdp_url`` is set.
            user_data_dir: persistent profile directory. When set, uses
                ``launch_persistent_context`` so cookies/history/extensions persist
                across runs. Cannot be shared by two browsers at the same time.
                A leading ``~`` is expanded to the user's home directory on all
                platforms (``~/.automation`` → ``/home/me/.automation`` or
                ``C:\\Users\\me\\.automation``).
            channel: Chromium channel (e.g. ``"chrome"``, ``"msedge"``). Uses the
                locally installed browser instead of Playwright's bundled Chromium.
            args: extra raw arguments passed to the Chromium process.
            cdp_url: connect to an already-running Chrome via CDP (e.g.
                ``"http://localhost:9222"`` or a Browserless/Browserbase ``wss://``
                endpoint) instead of launching one. Always opens a fresh tab so the
                user's existing tabs are untouched. Mutually exclusive with
                ``headless``/``user_data_dir``/``channel``/``args``.

        Returns a playwright Page object that can be passed to other methods.
        """
        launched = launch_browser(
            url,
            headless,
            viewport=viewport,
            user_data_dir=user_data_dir,
            channel=channel,
            args=args,
            cdp_url=cdp_url,
        )
        self._pw_instances.append(launched.playwright)
        if launched.cdp:
            self._cdp_pages.append(launched.page)
        return launched.page

    def _maybe_wait(
        self,
        target: Any,
        locate: str,
        timeout: float,
        interval: float,
        wait: str,
        thinking_level: str = "",
        language: str = "",
    ) -> None:
        """Auto-wait before an action: poll until the target looks present.

        When ``timeout > 0``, block until a visual assertion holds (or raise
        :class:`QirabotTimeoutError`). The assertion is ``wait`` if given, else
        one derived from ``locate``. This is qirabot's framework-agnostic
        analogue of Playwright's auto-waiting — but it can only check *visible*
        (a vision yes/no), not stable/enabled/receives-events. It deliberately
        polls an **assertion** (verify is honest) rather than the action's
        locate (which fabricates coordinates for absent elements).
        """
        if not timeout or timeout <= 0:
            return
        assertion = wait or f"the element/button for '{locate}' is visible on screen"
        self.wait_for(
            target,
            assertion,
            timeout=timeout,
            interval=interval,
            thinking_level=thinking_level,
            language=language,
        )

    def click(
        self,
        target: Any,
        locate: str,
        *,
        modifier: str = "",
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        """AI-powered click: locate element by description and click it.

        ``modifier`` holds modifier key(s) around the click (``"alt"``,
        ``"ctrl"``, ``"shift"``, ``"win"``; join several with ``+``, e.g.
        ``"ctrl+shift"``) — desktop backends only (pyautogui / the Windows
        Windows); other backends degrade to a plain click.

        When ``timeout > 0``, auto-waits until the element looks present before
        clicking (polling a visual assertion every ``interval`` seconds), and
        raises :class:`QirabotTimeoutError` if it never appears. ``wait`` lets
        you supply that assertion explicitly; otherwise it is derived from
        ``locate``. With the default ``timeout=0`` the click is immediate.

        Returns the current target (the same kind you passed in: a Playwright
        Page, Selenium/Appium driver, or the pyautogui module). If the click
        opened a link in a new tab, this is that new tab — reassign it
        (``page = bot.click(page, ...)``) to keep operating on the active page.
        """
        self._maybe_wait(
            target,
            locate,
            timeout,
            interval,
            wait,
            thinking_level=thinking_level,
            language=language,
        )
        adapter = self._get_adapter(target)
        params: dict[str, Any] = {"locate": locate}
        if modifier:
            params["modifier"] = modifier
        self._ai_action(
            target,
            action={"type": "click", "params": params},
            thinking_level=thinking_level,
            language=language,
            retry=retry,
        )
        return self._result(adapter)

    def type_text(
        self,
        target: Any,
        locate: str,
        text: str,
        *,
        press_enter: bool = False,
        clear_before_typing: bool = False,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        """AI-powered type: locate input field and type text.

        With an empty ``locate`` the text is typed into whatever currently has
        keyboard focus, deterministically (no AI, no billing) — like
        :meth:`press_key`. Use it when focus is already where you want it (a
        game chat box opened with Enter, a field reached via Tab, …); making
        sure focus is there is the caller's responsibility. ``press_enter`` and
        ``clear_before_typing`` still apply; ``timeout``/``wait``/``retry`` are
        ignored (there is no element to wait for).

        When ``timeout > 0``, auto-waits until the field looks present before
        typing (see :meth:`click` for the ``timeout``/``interval``/``wait``
        semantics). With the default ``timeout=0`` it types immediately.

        Returns the current target (same kind you passed in); reassign it
        (``page = bot.type_text(page, ...)``) to follow any tab switch.
        """
        adapter = self._get_adapter(target)
        params: dict[str, Any]
        if not locate:
            # Direct typing into the focused element — no AI, no billing.
            params = {"text": text}
            if press_enter:
                params["press_enter"] = True
            if clear_before_typing:
                params["clear_before_typing"] = True
            adapter.execute("type_text", params)  # no x/y -> focused path
            self._record_local_step(adapter, "type_text", params)
            return self._result(adapter)
        self._maybe_wait(
            target,
            locate,
            timeout,
            interval,
            wait,
            thinking_level=thinking_level,
            language=language,
        )
        params = {"locate": locate, "text": text}
        if press_enter:
            params["press_enter"] = True
        if clear_before_typing:
            params["clear_before_typing"] = True
        self._ai_action(
            target,
            action={"type": "type_text", "params": params},
            thinking_level=thinking_level,
            language=language,
            retry=retry,
        )
        return self._result(adapter)

    def double_click(
        self,
        target: Any,
        locate: str,
        *,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        """AI-powered double-click: locate element by description and double-click it.

        When ``timeout > 0``, auto-waits until the element looks present before
        acting (see :meth:`click` for the ``timeout``/``interval``/``wait``
        semantics). With the default ``timeout=0`` it acts immediately.

        Returns the current target (same kind you passed in); reassign it
        (``page = bot.double_click(page, ...)``) to follow any tab switch.
        """
        self._maybe_wait(
            target,
            locate,
            timeout,
            interval,
            wait,
            thinking_level=thinking_level,
            language=language,
        )
        adapter = self._get_adapter(target)
        self._ai_action(
            target,
            action={"type": "double_click", "params": {"locate": locate}},
            thinking_level=thinking_level,
            language=language,
            retry=retry,
        )
        return self._result(adapter)

    def long_press(
        self,
        target: Any,
        locate: str,
        *,
        duration: float = 2.0,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        """AI-powered long press: locate element and press-and-hold it.

        Touch-only gesture (Android/iOS) for context menus, edit/select mode,
        drag-to-reorder priming, etc. ``duration`` is the hold time in seconds
        (default 2.0).

        When ``timeout > 0``, auto-waits until the element looks present before
        acting (see :meth:`click` for the ``timeout``/``interval``/``wait``
        semantics). With the default ``timeout=0`` it acts immediately.

        Returns the current target (same kind you passed in).
        """
        self._maybe_wait(
            target,
            locate,
            timeout,
            interval,
            wait,
            thinking_level=thinking_level,
            language=language,
        )
        adapter = self._get_adapter(target)
        params: dict[str, Any] = {"locate": locate}
        if duration != 2.0:
            # Wire convention is milliseconds (matches the server schema/wait).
            params["duration"] = int(duration * 1000)
        self._ai_action(
            target,
            action={"type": "long_press", "params": params},
            thinking_level=thinking_level,
            language=language,
            retry=retry,
        )
        return self._result(adapter)

    def mouse_down(
        self,
        target: Any,
        locate: str,
        *,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        """AI-powered mouse press-and-hold: locate an element and hold the
        button down on it WITHOUT releasing (pairs with :meth:`mouse_up`).

        Desktop-only primitive for drag-from / press-and-hold gestures. You are
        responsible for the matching ``mouse_up``; as a safety net any input
        still held is auto-released at the end of an :meth:`ai` run and on
        :meth:`close`.

        When ``timeout > 0``, auto-waits until the element looks present before
        acting (see :meth:`click` for the semantics).

        Returns the current target (same kind you passed in).
        """
        self._maybe_wait(
            target,
            locate,
            timeout,
            interval,
            wait,
            thinking_level=thinking_level,
            language=language,
        )
        adapter = self._get_adapter(target)
        self._ai_action(
            target,
            action={"type": "mouse_down", "params": {"locate": locate}},
            thinking_level=thinking_level,
            language=language,
            retry=retry,
        )
        return self._result(adapter)

    def mouse_up(
        self,
        target: Any,
        locate: str = "",
        *,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        """Release the mouse button (pairs with :meth:`mouse_down`).

        With ``locate`` the element is found (AI, billed) and the cursor moves
        there before releasing — i.e. drop on a target. With the default empty
        ``locate`` it releases at the current cursor position deterministically
        (no AI, no billing), like :meth:`press_key`.

        Returns the current target (same kind you passed in).
        """
        adapter = self._get_adapter(target)
        if not locate:
            adapter.execute("mouse_up", {})
            self._record_local_step(adapter, "mouse_up")
            return self._result(adapter)
        self._maybe_wait(
            target,
            locate,
            timeout,
            interval,
            wait,
            thinking_level=thinking_level,
            language=language,
        )
        self._ai_action(
            target,
            action={"type": "mouse_up", "params": {"locate": locate}},
            thinking_level=thinking_level,
            language=language,
            retry=retry,
        )
        return self._result(adapter)

    def key_down(self, target: Any, key: str) -> Any:
        """Press and HOLD a key without releasing (pairs with :meth:`key_up`).
        No AI, no billing.

        Desktop-only primitive for held-key gestures (e.g. hold ``"w"`` to keep
        moving in a game, hold ``"shift"`` to modify clicks). You are
        responsible for the matching ``key_up``; any key still held is
        auto-released at the end of an :meth:`ai` run and on :meth:`close`.

        Returns the current target (same kind you passed in).
        """
        adapter = self._get_adapter(target)
        adapter.execute("key_down", {"key": key})
        self._record_local_step(adapter, "key_down", {"key": key})
        return self._result(adapter)

    def key_up(self, target: Any, key: str) -> Any:
        """Release a key previously held with :meth:`key_down`. No AI, no billing.

        Returns the current target (same kind you passed in).
        """
        adapter = self._get_adapter(target)
        adapter.execute("key_up", {"key": key})
        self._record_local_step(adapter, "key_up", {"key": key})
        return self._result(adapter)

    def extract(
        self,
        target: Any,
        instruction: str,
        *,
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> ExtractResult:
        """Extract data from the screen using AI.

        Returns an :class:`ExtractResult` — a str subclass that is the extracted
        text, with the call's token usage attached (``input_tokens`` /
        ``output_tokens`` / ``thinking_tokens``). Usable anywhere a str is.
        """
        result = self._ai_action(
            target,
            action={"type": "extract", "params": {"instruction": instruction}},
            thinking_level=thinking_level,
            language=language,
            execute_result=False,
            retry=retry,
        )
        return ExtractResult.from_dict(result)

    def verify(
        self,
        target: Any,
        assertion: str,
        *,
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> VerifyResult:
        """Verify a visual assertion.

        Returns a :class:`VerifyResult` that is truthy when the assertion holds,
        so ``assert bot.verify(...)`` keeps working; read ``reason`` for the
        model's explanation and the token fields for this call's usage.
        """
        result = self._ai_action(
            target,
            action={"type": "assert", "params": {"assertion": assertion}},
            thinking_level=thinking_level,
            language=language,
            execute_result=False,
            retry=retry,
        )
        return VerifyResult.from_dict(result)

    def locate(
        self,
        target: Any,
        locate: str,
        *,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> LocateResult:
        """Resolve an element description to coordinates without acting.

        Returns a :class:`LocateResult` whose ``x``/``y`` are in the adapter's
        screenshot pixel space (window-relative on the Windows window backend,
        physical screen pixels on pyautogui, device pixels on mobile). Supports
        tuple unpacking: ``x, y = bot.locate(page, "the OK button")``. Nothing
        is clicked or typed — feed the coordinates to your own framework calls.

        When ``timeout > 0``, auto-waits until the element looks present before
        locating, with the same semantics as :meth:`click` (``wait`` overrides
        the polled assertion; each poll is an LLM verify call and billed as
        such). The locate itself is a single vision call.

        WARNING: the resolver returns coordinates even for elements that are
        not on screen, and those coordinates are unreliable. Pass ``timeout``
        or check with :meth:`verify`/:meth:`wait_for` first when presence is
        not guaranteed.
        """
        self._maybe_wait(
            target,
            locate,
            timeout,
            interval,
            wait,
            thinking_level=thinking_level,
            language=language,
        )
        result = self._ai_action(
            target,
            action={"type": "locate", "params": {"locate": locate}},
            thinking_level=thinking_level,
            language=language,
            execute_result=False,
            retry=retry,
        )
        return LocateResult.from_dict(result)

    def _accumulate_tokens(self, result: dict[str, Any]) -> None:
        """Fold a /act response's token/timing usage into the run stats —
        error payloads included: a failed step's decide attempts (up to
        MAX_GROUNDING_ATTEMPTS of them) are real spend the engine reports on
        the error body, and dropping them would understate the session totals
        exactly on the most expensive steps."""
        self._stats["input_tokens"] += result.get("inputTokens", 0)
        self._stats["output_tokens"] += result.get("outputTokens", 0)
        self._stats["thinking_tokens"] += result.get("thinkingTokens", 0)
        self._stats["cache_read_tokens"] += result.get("cacheReadTokens", 0)
        self._stats["cache_write_tokens"] += result.get("cacheWriteTokens", 0)
        self._stats["step_duration_ms"] += result.get("stepDurationMs", 0)
        self._stats["llm_decision_duration_ms"] += result.get("llmDecisionDurationMs", 0)

    def _accumulate_stats(self, result: dict[str, Any]) -> None:
        """A successful AI call: its usage plus one AI step.

        ai_steps counts committed calls only (matching the report's step
        count); failed calls go through _accumulate_tokens alone.
        """
        self._stats["ai_steps"] += 1
        self._accumulate_tokens(result)

    def wait_for(
        self,
        target: Any,
        assertion: str,
        timeout: float = 30.0,
        interval: float = 2.0,
        *,
        thinking_level: str = "",
        language: str = "",
    ) -> None:
        """Wait until a visual condition holds, polling every ``interval`` seconds.

        Acts as an assertion/gate: returns once the condition is met, or raises
        :class:`QirabotTimeoutError` if it is still not met after ``timeout``
        seconds. For a non-raising one-shot check use :meth:`verify`, which
        returns a bool.
        """
        deadline = time.monotonic() + timeout
        while True:
            met = self.verify(
                target,
                assertion,
                thinking_level=thinking_level,
                language=language,
            )
            if met:
                return
            if time.monotonic() >= deadline:
                raise QirabotTimeoutError(
                    f"wait_for timed out after {timeout:g}s: {assertion}"
                )
            time.sleep(interval)

    def ai(
        self,
        target: Any,
        instruction: str,
        max_steps: int = 20,
        *,
        on_step: Callable[[StepResult], None] | None = None,
        thinking_level: str = "",
        language: str = "",
        custom_tools: list[Callable[..., Any] | dict[str, Any]] | None = None,
        exclude_tools: list[str] | None = None,
        knowledge: str | Path | list[str | Path] | None = None,
    ) -> RunResult:
        """AI-powered multi-step operation.

        Steps run by this call are grouped under ``instruction`` in the report.

        ``custom_tools`` registers your own functions as tools the model can
        call mid-task (e.g. a GM-command sender in game testing). Pass named
        functions — tool name, description, and parameters come from the
        function name, docstring, and signature — or dicts with an explicit
        schema plus a ``handler`` callable. When the model picks one, the SDK
        calls it locally and feeds the return value back to the model as the
        observation; tools run on your machine only, never server-side.

        ``exclude_tools`` removes built-in tools (by name, e.g. ``"scroll"``)
        from the model's tool list for this call; ``done`` cannot be excluded.

        ``knowledge`` supplies domain background the model consults while
        deciding (game rules, business flows, terminology) — kept separate
        from ``instruction`` so reference material is never mistaken for the
        goal. Pass the text itself, a local file as ``pathlib.Path`` (UTF-8),
        or a list mixing both. For remote sources, fetch the text yourself
        (e.g. ``requests.get(url).text``) and pass it. Applies to this call
        only, so per-stage knowledge loads with its stage and drops with it.
        Hard limits (e.g. "GM may be used once") belong in the custom tool's
        handler code, not here — prompts persuade, code enforces.
        """
        if self._user_aborted:
            # A previous run on this client was aborted by the user. Don't
            # light the glow, don't take a screenshot, don't touch the
            # machine: continuing requires the script to acknowledge via
            # clear_user_abort().
            raise QirabotError(
                "a previous run was aborted by the user; call "
                "clear_user_abort() to allow further ai() runs",
                code="user_abort",
            )
        prev_section = self._current_section
        section = instruction or "ai"
        runs = self._section_runs.get(section, 0) + 1
        self._section_runs[section] = runs
        if runs > 1:
            section = f"{section} #{runs}"
        self._current_section = section
        if self._overlay is not None:
            # Edge glow only when the run takes over the REAL mouse/keyboard
            # (desktop backends): the "hands off" signal would be a lie for
            # remote-protocol targets. Adapter resolution can fail for a bad
            # target — the loop below surfaces that; the overlay never does.
            edge_glow = False
            try:
                edge_glow = bool(self._get_adapter(target).controls_user_input)
            except Exception:
                pass
            self._overlay.begin(instruction, edge_glow=edge_glow)
        try:
            result = self._ai_loop(
                target,
                instruction,
                max_steps,
                on_step=on_step,
                thinking_level=thinking_level,
                language=language,
                custom_tools=custom_tools,
                exclude_tools=exclude_tools,
                knowledge=knowledge,
            )
            self._section_outcomes[self._current_section] = result.status
            self._last_ai_status = result.status
            self._last_ai_error = result.output if result.status == "error" else ""
            if self._overlay is not None:
                self._overlay.finish(result.success, result.output or result.status)
            return result
        except Exception as e:
            aborted = (
                getattr(e, "code", "") == "user_abort"
                or type(e).__name__ == "FailSafeException"
            )
            if aborted:
                # A deliberate user abort (ESC hold, mouse-to-corner) is a
                # cancellation, not a bot failure: record the server's
                # distinct 'cancelled' terminal state — same bucket as a
                # Ctrl+C routed through cancel(), kept out of failure
                # metrics. cancel()'s terminalized guard also stops close()
                # from re-recording the task as failed.
                self._section_outcomes[self._current_section] = "cancelled"
                self._last_ai_status = "cancelled"
                self._last_ai_error = str(e)
                self._user_aborted = True  # sticky: see clear_user_abort()
                self.cancel(str(e))
            else:
                # Any other exception on the way out — ActionError, timeout,
                # adapter failure — is an "error" ending, distinct from
                # goal_failed.
                self._section_outcomes[self._current_section] = "error"
                self._last_ai_status = "error"
                self._last_ai_error = str(e)
            if self._overlay is not None:
                self._overlay.finish(False, str(e))
            raise
        finally:
            self._current_section = prev_section
            # Safety net: release any mouse button / key the model held with
            # mouse_down/key_down but never released (or that an exception
            # interrupted), so a stuck input can't corrupt later actions or
            # outlive this run. Best-effort; never mask the real result/error.
            try:
                self._get_adapter(target).release_all_inputs()
            except Exception:
                logger.debug("release_all_inputs failed after ai()", exc_info=True)

    def _ai_loop(
        self,
        target: Any,
        instruction: str,
        max_steps: int = 20,
        *,
        on_step: Callable[[StepResult], None] | None = None,
        thinking_level: str = "",
        language: str = "",
        custom_tools: list[Callable[..., Any] | dict[str, Any]] | None = None,
        exclude_tools: list[str] | None = None,
        knowledge: str | Path | list[str | Path] | None = None,
    ) -> RunResult:
        adapter = self._get_adapter(target)
        steps: list[StepResult] = []
        last_action_result = ""
        last_was_save_note = False
        tool_defs, tool_handlers = build_tool_defs(custom_tools) if custom_tools else ([], {})
        knowledge_text = resolve_knowledge(knowledge) if knowledge is not None else ""
        sent_tool_params = bool(tool_defs or exclude_tools)

        for step_num in range(1, max_steps + 1):
            self._raise_if_user_abort()
            # After save_note the device hasn't moved, so reuse the cached
            # screenshot on the server side and skip a redundant upload.
            if last_was_save_note:
                screenshot_bytes = b""
            else:
                screenshot_bytes = adapter.screenshot(self._screenshot_config)
            device_info = adapter.device_info()

            request_body: dict[str, Any] = {
                "device_info": device_info.to_dict(),
            }
            if last_action_result:
                request_body["action_result"] = last_action_result
            if step_num == 1:
                ai_params: dict[str, Any] = {"instruction": instruction, "max_steps": max_steps}
                if tool_defs:
                    ai_params["custom_tools"] = tool_defs
                if exclude_tools:
                    ai_params["exclude_tools"] = list(exclude_tools)
                if knowledge_text:
                    ai_params["knowledge"] = knowledge_text
                request_body["action"] = {
                    "type": "ai",
                    "params": ai_params,
                }
            else:
                # Continuation steps still carry the action envelope so the
                # backend routes them to the live ai session.
                request_body["action"] = {"type": "ai", "params": {}}
            tl = thinking_level or self._thinking_level
            if tl:
                request_body["thinking_level"] = tl
            lang = language or self._language
            if lang:
                request_body["language"] = lang

            result = self._backend.act(
                screenshot_bytes, request_body, self._screenshot_config.mime_type
            )

            if not result.get("success"):
                # The engine attaches the failed step's usage to the error
                # body (its decide attempts are real spend) — keep the
                # tokens, but no step: none committed.
                self._accumulate_tokens(result)
                error_msg = result.get("error", "AI request failed")
                if result.get("finished"):
                    logger.error("failed: %s", error_msg)
                    # Surface the failure reason as a section banner, NOT a
                    # synthetic step entry: no step committed server-side, so
                    # adding one locally would make the report's step count
                    # disagree with the cloud timeline.
                    self._section_errors[self._current_section] = error_msg
                    return RunResult(
                        success=False, output=error_msg, steps=steps, status="error"
                    )
                raise ActionError(error_msg)

            if warning := result.get("warning"):
                logger.warning("%s", warning)
            if step_num == 1 and sent_tool_params:
                # Only successful responses go through NewStepResponse, so the
                # echo's absence there is meaningful; error bodies never carry it.
                registration = result.get("tool_registration")
                if registration:
                    logger.info(
                        "custom tools registered: %s; excluded: %s",
                        registration.get("registered") or [],
                        registration.get("excluded") or [],
                    )
                else:
                    logger.warning(
                        "server does not support custom_tools/exclude_tools; "
                        "tools will not take effect"
                    )
            if step_num == 1 and knowledge_text:
                # Same old-server detection as tools: only successful responses
                # go through NewStepResponse, so a missing echo there means the
                # server ignored the param — warn instead of degrading silently.
                registered = result.get("knowledge_registered")
                if registered is None:
                    logger.warning(
                        "server does not support knowledge; it will not take effect"
                    )
                else:
                    logger.info("knowledge registered: %d bytes", registered)

            action_type = result.get("actionType")
            action_params = result.get("params") or {}
            finished = result.get("finished", False)
            decision = result.get("decision", "")

            coords = _extract_coords(action_params)
            entry = self._record_step(
                screenshot_bytes,
                action_type or "ai",
                action_params,
                coords,
                end_coords=_extract_end_coords(action_params),
                output=result.get("output", ""),
                finished=finished,
                decision=decision,
                coord_scale=adapter.annotation_scale(),
            )

            if logger.isEnabledFor(logging.INFO):
                parts = [f"step {step_num}/{max_steps}"]
                if decision:
                    parts.append(decision)
                parts.append(f"-> {action_type}")
                detail_parts = []
                if "locate" in action_params:
                    detail_parts.append(f'"{action_params["locate"]}"')
                if "text" in action_params:
                    detail_parts.append(f'text="{action_params["text"]}"')
                if "direction" in action_params:
                    detail_parts.append(f'{action_params["direction"]} {action_params.get("amount", "")}')
                if detail_parts:
                    parts.append(f"({', '.join(detail_parts)})")
                logger.info("%s", " ".join(parts))

            step_result = StepResult.from_dict(result, step_num)
            steps.append(step_result)

            self._accumulate_stats(result)

            if self._overlay is not None:
                self._overlay.step(step_result, max_steps)
            if on_step:
                on_step(step_result)

            if finished:
                output = result.get("output", "")
                # The done action carries the model's own success flag: false
                # means it concluded the goal is unreachable (login wall,
                # captcha, the app froze). It rides in the action params; the
                # top-level `success` checked above only means "the step
                # committed". The server records this same outcome as the task's
                # terminal state (mirroring max-steps). Default true for older
                # servers that omit the flag.
                goal_ok = bool(action_params.get("success", True))
                # Log a short completion marker, not the full output: the result
                # text is the caller's to surface via result.output, and dumping
                # it here duplicates that for any caller that prints the result
                # (and is out of step with the short per-step progress lines).
                logger.info("completed in %d step(s)", len(steps))
                return RunResult(
                    success=goal_ok,
                    output=output,
                    steps=steps,
                    status="completed" if goal_ok else "goal_failed",
                )

            if action_type and action_type != "done":
                # Second abort checkpoint, right before injection: ESC held
                # while the model was thinking must stop THIS action, not
                # the next one — "I hit the kill switch and it clicked once
                # more anyway" is the worst version of a slow abort. The
                # in-flight server call above is the only wait that remains.
                self._raise_if_user_abort()
                try:
                    if action_type in tool_handlers:
                        # Custom tool: run the user's handler instead of a
                        # device action. Params are exactly the model's args
                        # for the registered schema (the server strips its
                        # meta fields). The return value is the observation
                        # fed back to the model on the next request —
                        # "ok" if result is None, NOT str(result) or "ok":
                        # str(None) is the truthy string "None".
                        ret = tool_handlers[action_type](**action_params)
                        last_action_result = "ok" if ret is None else str(ret)
                    else:
                        self._execute_action(adapter, result)
                        last_action_result = "ok"
                except Exception as e:
                    if type(e).__name__ == "FailSafeException":
                        # pyautogui's corner kill switch: the USER slammed the
                        # mouse into a screen corner to abort. Feeding it back
                        # as a recoverable action error would have the model
                        # retry — moving the mouse out of the corner and
                        # defeating the abort. Propagate: ai()'s finally
                        # releases held inputs and the run ends here. (Name
                        # check, not isinstance: pyautogui is an optional
                        # dependency this module never imports.)
                        raise
                    last_action_result = f"ERROR: {e}"
                    # The step's screenshot/decision were recorded before this
                    # action ran, so its outcome only surfaces now. Backfill the
                    # entry so the report marks it failed (red ✗) and shows why,
                    # instead of leaving an errored step looking successful. The
                    # loop still continues — the error is fed back so the model
                    # can recover on the next step.
                    if entry is not None:
                        entry["success"] = False
                        err = f"execution failed: {e}"
                        entry["output"] = (
                            f"{entry['output']}\n{err}" if entry["output"] else err
                        )

            last_was_save_note = action_type == "save_note"

        # A truncation, not an error: the budget ran out before the model
        # finished. warning-level, and surfaced as an amber section banner
        # rather than a synthetic step entry — the server records the same
        # outcome on the command, not as a step, and the report's step count
        # must match the cloud timeline.
        logger.warning("stopped: step budget exhausted (%d/%d)", max_steps, max_steps)
        self._section_errors[self._current_section] = f"max steps reached ({max_steps})"
        # Output string is load-bearing: callers may match "max steps reached".
        return RunResult(
            success=False, output="max steps reached", steps=steps, status="max_steps"
        )

    def screenshot(self, target: Any) -> Path | None:
        """Take a screenshot and save it to ``report_dir/screenshots/``.

        Returns the saved file path, or ``None`` when ``report=False``. No AI,
        no billing.
        """
        adapter = self._get_adapter(target)
        data = adapter.screenshot(self._screenshot_config)
        return self._save_frame(data, "manual")

    def _maybe_start_recording(self, target: Any = None) -> None:
        """Auto-start screen recording when ``record=True``.

        Called once from ``__init__`` (no ``target``) and again from
        :meth:`_get_adapter` on every action (with the action's ``target``).
        Skipped once a recorder exists or the slot has been claimed. In
        ``record_window`` mode the start is deferred until an action supplies a
        ``target`` to resolve the window from. Best-effort via
        :meth:`start_recording` — a missing ffmpeg / unsupported platform warns.
        """
        if self._recorder is not None or not self._record_pending:
            return
        if (
            (self._record_window or self._record_device)
            and target is None
            and not self._record_mjpeg_url
        ):
            return  # defer: need a target to resolve the window/device from
        # Claim the slot BEFORE starting so the _get_adapter() call made while
        # resolving the window doesn't re-enter this and start a second ffmpeg.
        self._record_pending = False
        self.start_recording(target=target)

    def _resolve_window_target(self, target: Any) -> str | None:
        """Window title (or handle) to record for ``target``, or ``None``.

        Reads the adapter's :meth:`~qirabot.adapters.base.DeviceAdapter.window_info`
        (only the Windows window backend returns one); prefers the title, falls back to the
        numeric handle. Any failure degrades to ``None`` (full-screen).
        """
        try:
            info = self._get_adapter(target).window_info()
        except Exception:
            logger.debug("window_info() failed; recording full screen", exc_info=True)
            return None
        if not info:
            return None
        title = info.get("title")
        if title:
            return str(title)
        hwnd = info.get("hwnd")
        return str(hwnd) if hwnd is not None else None

    def _resolve_window_region(self, target: Any) -> tuple[int, int, int, int] | None:
        """Visible (x, y, w, h) of ``target``'s window for desktop-crop recording.

        Reads the adapter's ``window_info()`` hwnd and resolves its physical-px
        rect via :func:`qirabot.recording.window_region`. Returns ``None`` (so
        the caller degrades to per-window or full-screen) when there's no hwnd or
        the rect can't be resolved.
        """
        try:
            info = self._get_adapter(target).window_info()
        except Exception:
            logger.debug("window_info() failed; not using region capture", exc_info=True)
            return None
        hwnd = info.get("hwnd") if info else None
        if hwnd is None:
            return None
        from qirabot.recording import window_region

        return window_region(int(hwnd))

    def start_recording(
        self,
        *,
        fps: int | None = None,
        target: Any = None,
        window: str | None = None,
        audio: bool | str | None = None,
    ) -> bool:
        """Start ffmpeg recording into ``report_dir/recording.mp4``.

        Records the full screen by default. Two settings switch it to the
        *device's* screen instead (window/audio options below don't apply):
        ``record_mjpeg_url`` (or ``QIRA_RECORD_MJPEG_URL``) records that MJPEG
        stream — e.g. WDA's iOS device-screen stream on port 9100 — and
        ``record_device`` (or ``QIRA_RECORD_DEVICE``) picks a recorder from the
        action ``target``: an Appium driver uses the session recording API
        (stopped automatically before the report; callers quitting the driver
        themselves must call :meth:`stop_recording` first), an AdbDevice
        device uses ``adb screenrecord``. On Windows it can instead follow a
        single window and capture system audio:

        * ``window`` — a window title (or numeric handle) to record via legacy
          per-window capture.
        * ``target`` — when ``record_window`` is set, the window is resolved
          automatically from this action target (Windows window backend only). By
          default its visible rect is cropped out of a desktop grab (works for
          GPU/game windows); set ``QIRA_RECORD_WINDOW_NATIVE=1`` to force the
          legacy per-window mode instead.
        * ``audio`` — ``True`` to auto-detect a system-audio device, a dshow
          device name, or ``False``; defaults to the ``record_audio`` setting.

        Idempotent: if a recording is already running, this is a no-op returning
        ``True``. The file is finalized and embedded in the report on
        :meth:`close`. Best-effort — returns ``False`` (and only warns) when
        ffmpeg is missing or the platform is unsupported.

        Note: starting again after :meth:`stop_recording` overwrites the same
        ``recording.mp4`` (it re-records from scratch, it does not resume).
        """
        if self._recorder is not None and self._recorder.active:
            logger.info("recording already in progress; ignoring start_recording()")
            return True
        # Manual start also claims the slot so a later action's auto-start hook
        # doesn't spawn a second recorder.
        self._record_pending = False
        output = os.path.join(self.report_dir, "recording.mp4")
        recorder: Recorder | None
        if self._record_mjpeg_url:
            # Device-screen stream (WDA MJPEG): window/region/audio are
            # host-screen concepts and don't apply.
            recorder = MjpegStreamRecorder(output, self._record_mjpeg_url)
        elif self._record_device:
            # Device-screen recording resolved from the action target (Appium
            # driver / AdbDevice). Falling back to the host screen would
            # record the wrong thing (the desktop the SDK runs on), so an
            # unsupported target skips recording — the report then carries the
            # requested-but-not-produced notice.
            recorder = device_recorder(output, target)
            if recorder is None:
                logger.warning(
                    "record: don't know how to record the device screen for %s "
                    "(need an Appium driver or an AdbDevice target); recording skipped",
                    type(target).__name__,
                )
                return False
        else:
            region: tuple[int, int, int, int] | None = None
            if window is None and target is not None and self._record_window:
                # Default: crop a desktop grab to the window's visible rect (GPU/game
                # safe). Fall back to legacy per-window capture when forced via
                # QIRA_RECORD_WINDOW_NATIVE or when the rect can't be resolved
                # (non-Windows, no hwnd, DWM off).
                if not self._record_window_native:
                    region = self._resolve_window_region(target)
                if region is None:
                    window = self._resolve_window_target(target)
            audio_spec = audio if audio is not None else self._record_audio
            recorder = ScreenRecorder(
                output,
                fps=fps if fps is not None else self._record_fps,
                window=window,
                region=region,
                audio=audio_spec,
                audio_offset=self._record_audio_offset,
            )
        started = recorder.start()
        self._recorder = recorder if started else None
        if started:
            # A restart overwrites recording.mp4 from scratch, so the anchor
            # moves with it.
            self._record_started_ts = time.time()
        return started

    def stop_recording(self) -> str | None:
        """Stop the current recording and return the saved path (or ``None``).

        A no-op returning ``None`` when nothing is recording.
        """
        if self._recorder is None:
            return None
        recorder = self._recorder
        self._recorder = None
        try:
            return recorder.stop()
        except Exception:
            logger.debug("failed to stop recording", exc_info=True)
            return None

    def launch_app(self, app: str, *, wait: float = 2.0) -> None:
        """Launch (or activate) a desktop application before driving it.

        Convenience wrapper over :func:`qirabot.launch_app` for desktop
        (pyautogui) automation, which otherwise has no way to open an app. No
        AI, no billing. See that function for platform behaviour (macOS ``open``,
        Windows ``start``/``startfile``, Linux exec) and the ``app``/``wait``
        semantics.
        """
        from qirabot._applaunch import launch_app

        launch_app(app, wait=wait)

    def go_back(self, target: Any) -> Any:
        """Navigate back to the previous page/screen. No AI, no billing.

        On Playwright this is smart about tabs: if the current page has back
        history it goes back in place; if it doesn't (e.g. a click opened a link
        in a NEW tab, which starts with no history) and another tab exists, it
        closes the current tab and returns to the previous one.

        Supported on browser (Playwright, Selenium) and mobile (Appium)
        targets. Desktop (pyautogui) has no back concept and raises
        ``NotImplementedError``.

        Returns the current page/target (may differ after the navigation).
        """
        adapter = self._get_adapter(target)
        adapter.go_back()
        self._record_local_step(adapter, "go_back")
        return self._result(adapter)

    def close_tab(self, target: Any) -> Any:
        """Close the current browser tab and switch to the remaining one.

        Use this (not :meth:`go_back`) when a click opened a link in a NEW tab:
        a fresh tab has no history, so ``go_back`` is a no-op there — closing it
        is what returns you to the previous tab. No AI, no billing.

        Playwright only; other targets raise ``NotImplementedError``.

        Returns the now-current page after switching back.
        """
        adapter = self._get_adapter(target)
        adapter.close_tab()
        self._record_local_step(adapter, "close_tab")
        return self._result(adapter)

    def navigate(self, target: Any, url: str) -> Any:
        """Navigate the target to ``url``. No AI, no billing.

        If ``url`` has no scheme, ``https://`` is prepended. Supported on
        browser (Playwright, Selenium) and mobile (Appium) targets; desktop
        (pyautogui) raises ``NotImplementedError``.

        Returns the current page/target (may differ after the navigation).
        """
        if "://" not in url:
            url = "https://" + url
        adapter = self._get_adapter(target)
        adapter.navigate(url)
        self._record_local_step(adapter, "navigate", {"url": url})
        return self._result(adapter)

    def scroll(self, target: Any, direction: str = "down", distance: int = 3, *, x: float | None = None, y: float | None = None) -> None:
        """Scroll the target. No AI, no billing.

        Supported on all platforms (browser, mobile, desktop). ``direction`` is
        one of ``"up"``/``"down"``/``"left"``/``"right"``; ``distance`` is in
        scroll units (roughly ``distance * 100`` px). By default scrolls at the
        viewport center; pass ``x``/``y`` (screenshot pixels) to scroll at a
        specific point.
        """
        adapter = self._get_adapter(target)
        if x is None or y is None:
            info = adapter.device_info()
            x = info.width / 2 if x is None else x
            y = info.height / 2 if y is None else y
        adapter.scroll(float(x), float(y), direction, int(distance))
        self._record_local_step(
            adapter, "scroll",
            {"direction": direction, "amount": distance}, (float(x), float(y)),
        )

    def press_key(self, target: Any, key: str, duration_seconds: float = 0) -> Any:
        """Press a key or key combo. No AI, no billing.

        ``key`` is a single key (``"Enter"``, ``"Escape"``, ``"ArrowDown"``) or a
        combo joined with ``+`` (``"ctrl+c"``, ``"alt+tab"``). Each backend maps
        the name to its own vocabulary, so the same call works across Playwright,
        Selenium, Appium, adb, WDA, Windows and pyautogui — Android/iOS take single keycodes
        (``"Back"``/``"Home"``/``"Enter"``); ctrl-style combos are desktop/browser
        only.

        ``duration_seconds`` > 0 holds the key(s) for that long before releasing
        (blocking call), for game-style movement where an instant tap is too
        short. Desktop backends only (pyautogui, the Windows window backend); other
        backends degrade to an instant tap. Clamped to 10 seconds.

        Returns the current target (same kind you passed in). On Playwright a
        combo that opens/closes a tab (``ctrl+t``/``ctrl+w``) switches the active
        page, so reassign it (``page = bot.press_key(page, "ctrl+t")``).
        """
        adapter = self._get_adapter(target)
        params: dict[str, Any] = {"key": key}
        if duration_seconds > 0:
            params["duration_seconds"] = duration_seconds
        adapter.execute("press_key", params)
        self._record_local_step(adapter, "press_key", params)
        return self._result(adapter)

    def _record_step(
        self,
        data: bytes,
        action_type: str,
        params: dict[str, Any] | None,
        coords: tuple[float, float] | None = None,
        *,
        end_coords: tuple[float, float] | None = None,
        output: str = "",
        finished: bool = False,
        success: bool = True,
        warn: bool = False,
        decision: str = "",
        coord_scale: float = 1.0,
    ) -> dict[str, Any] | None:
        """Save the screenshot (if reporting) and append a step to the timeline.

        Returns the appended log entry so the caller can backfill fields that
        only become known after recording (e.g. an action's execution result),
        or ``None`` when reporting is off and nothing was recorded.

        ``assert`` actions (verify / wait_for polls) are recorded like any other
        step: the server keeps them anyway, and the poll frames are the key
        evidence when a ``wait_for`` times out.
        """
        # Reporting off → zero overhead.
        if not self._report:
            return None
        # Annotation + thumbnailing share a single PIL decode; never let a
        # malformed/unexpected screenshot break the actual action — degrade to
        # the raw bytes / no thumbnail instead.
        annotated = data
        thumb = ""
        if data:
            try:
                annotated, thumb = _render_step_images(
                    data,
                    coords,
                    self._screenshot_config,
                    end_coords=end_coords,
                    coord_scale=coord_scale,
                )
            except Exception:
                logger.debug("render step images failed", exc_info=True)
        frame = self._save_frame(annotated, action_type or "action") if data else None
        entry: dict[str, Any] = {
            "section": self._current_section,
            "ts": time.time(),
            "action_type": action_type or "",
            "params": params or {},
            "decision": decision or "",
            "output": output or "",
            "finished": bool(finished),
            "success": bool(success),
            "coords": list(coords) if coords else None,
            # relative to report_dir so the html can link it directly
            "screenshot": f"screenshots/{frame.name}" if frame else "",
            "thumb": thumb,
        }
        # warn marks a truncation (max steps), not a failure — the report
        # renders it amber instead of red. Only set when true to keep the log
        # lean and older entries unchanged.
        if warn:
            entry["warn"] = True
        self._log.append(entry)
        return entry

    def _record_local_step(
        self,
        adapter: DeviceAdapter,
        action_type: str,
        params: dict[str, Any] | None = None,
        coords: tuple[float, float] | None = None,
    ) -> None:
        """Record a deterministic (non-AI) action in the local report.

        Primitives like :meth:`press_key` / :meth:`scroll` drive the adapter
        directly and bypass ``/act``, so the server never sees them and they
        were previously invisible in the report. Capture a post-action
        screenshot and append a step, mirroring what :meth:`_ai_action` does
        for AI actions. Best-effort: reporting off → zero overhead, and a
        failure to capture or persist the frame must never break the action
        itself — recording is a side channel, not part of the operation.

        Also the one funnel every direct-drive primitive passes through, so
        the edge-glow pulse for real-input calls lives here (before the
        reporting-off early return — the glow is not a reporting feature).
        """
        self._pulse_edge_glow(adapter)
        if not self._report:
            return
        try:
            data = adapter.screenshot(self._screenshot_config)
            # Only record once we actually have image bytes; anything else
            # (a stubbed adapter, a backend returning None) is skipped rather
            # than written to disk.
            if isinstance(data, (bytes, bytearray)):
                raw = bytes(data)
                self._record_step(
                    raw,
                    action_type,
                    params or {},
                    coords,
                    end_coords=_extract_end_coords(params),
                    coord_scale=adapter.annotation_scale(),
                )
        except Exception:
            logger.debug("local step recording failed", exc_info=True)

    def _save_frame(self, data: bytes, label: str) -> Path | None:
        """Write a full-resolution screenshot to ``report_dir/screenshots/``."""
        if not self._report:
            return None
        dir_path = self._report_dir / "screenshots"
        dir_path.mkdir(parents=True, exist_ok=True)
        self._screenshot_counter += 1
        filename = f"{self._screenshot_counter:03d}_{label}.{self._screenshot_config.extension}"
        path = dir_path / filename
        path.write_bytes(data)
        logger.debug("screenshot saved: %s", path)
        return path

    def current_page(self, target: Any) -> Any:
        """Return the actual current page/target (may differ from the original after tab switches)."""
        return self._result(self._get_adapter(target))

    def _get_adapter(self, target: Any) -> DeviceAdapter:
        adapter = self._adapters.get(id(target))
        if adapter is None:
            adapter = auto.detect(target)
            if self._settle_seconds is not None:
                adapter._settle_override = self._settle_seconds
            self._cache_adapter(target, adapter)
        # Deferred recording start for record_window mode: the first action to
        # supply a target lets us resolve the window to follow. Cheap no-op once
        # started/claimed (and re-entrancy-safe via _record_pending).
        if self._record_pending:
            self._maybe_start_recording(target)
        return adapter

    def _cache_adapter(self, target: Any, adapter: DeviceAdapter) -> None:
        """Cache ``adapter`` under ``id(target)``, evicting the entry when the
        target is garbage-collected.

        The cache is keyed by ``id()`` because targets aren't always hashable.
        Without eviction that has two failure modes: the dict grows unbounded
        as a long session churns through tabs/pages, and — worse — once a target
        is collected CPython can hand its ``id()`` to an unrelated object, so a
        stale adapter would be returned for it. A weakref finalizer drops the
        entry the moment the target dies, which bounds the cache and closes the
        id-reuse window. Targets that don't support weak references (rare) fall
        back to plain, un-evicted caching.
        """
        key = id(target)
        if key not in self._adapters:
            try:
                weakref.finalize(target, self._adapters.pop, key, None)
            except TypeError:
                pass  # target not weak-referenceable; keep plain caching
        self._adapters[key] = adapter

    def _result(self, adapter: DeviceAdapter) -> Any:
        """Return the adapter's current target, keeping the cache in sync.

        After a tab switch the active page is a *different* object than the one
        originally passed in. Adapters are cached by ``id(target)``, so if the
        caller passes that new page back (the common ``page = bot.click(page,
        ...)`` pattern), ``_get_adapter`` would otherwise spawn a second adapter
        that tracks its tabs independently and drifts out of sync (e.g. holding a
        tab another adapter has closed). Registering the returned object against
        this same adapter keeps exactly one adapter following the active tab.
        """
        target = adapter.current_target
        self._cache_adapter(target, adapter)
        return target

    def _ai_action(
        self,
        target: Any,
        action: dict[str, Any],
        thinking_level: str = "",
        language: str = "",
        execute_result: bool = True,
        retry: int | None = None,
    ) -> dict[str, Any]:
        """Run a single AI action through the local engine, retrying
        retryable failures (the engine's provider layer already retries
        transport blips; this loop only catches errors surfaced as retryable
        QirabotErrors)."""
        max_attempts = (retry if retry is not None else self._retry) + 1

        for attempt in range(max_attempts):
            try:
                return self._ai_action_once(
                    target, action,
                    thinking_level=thinking_level,
                    language=language,
                    execute_result=execute_result,
                )
            except QirabotError as e:
                if not _is_retryable(e) or attempt >= max_attempts - 1:
                    raise
                delay = self._retry_delay * (2 ** attempt)
                logger.warning(
                    "attempt %d/%d failed: %s, retrying in %.1fs...",
                    attempt + 1, max_attempts, e, delay,
                )
                time.sleep(delay)

        raise RuntimeError("unreachable")

    def _ai_action_once(
        self,
        target: Any,
        action: dict[str, Any],
        thinking_level: str = "",
        language: str = "",
        execute_result: bool = True,
    ) -> dict[str, Any]:
        """Single attempt of an AI action request."""
        adapter = self._get_adapter(target)
        screenshot_bytes = adapter.screenshot(self._screenshot_config)
        device_info = adapter.device_info()

        request_body: dict[str, Any] = {
            "action": action,
            "device_info": device_info.to_dict(),
        }
        tl = thinking_level or self._thinking_level
        if tl:
            request_body["thinking_level"] = tl
        lang = language or self._language
        if lang:
            request_body["language"] = lang

        result = self._backend.act(
            screenshot_bytes, request_body, self._screenshot_config.mime_type
        )

        # Every one-shot AI call funnels through here — AI-located actions
        # (click/type_text/…), verify/extract/locate — so this is the one
        # place their usage reaches the session totals. Failed calls (each
        # retry attempt lands here again) keep their tokens, just not a step.
        if not result.get("success"):
            self._accumulate_tokens(result)
            raise ActionError(result.get("error", "AI request failed"))
        self._accumulate_stats(result)

        coords = _extract_coords(result.get("params"))
        self._record_step(
            screenshot_bytes,
            result.get("actionType") or action.get("type", "action"),
            result.get("params") or action.get("params") or {},
            coords,
            end_coords=_extract_end_coords(result.get("params")),
            output=result.get("output", ""),
            finished=result.get("finished", False),
            success=result.get("success", True),
            coord_scale=adapter.annotation_scale(),
        )

        if execute_result and result.get("actionType"):
            self._execute_action(adapter, result)

        return result

    def _raise_if_user_abort(self) -> None:
        """End the run if the user hit the kill switch (ESC held while the
        edge glow was on — see Overlay's abort channel).

        Checked at every point the loop is about to spend time or inject
        input: the top of each step and again right before the action runs.
        """
        if self._overlay is not None and self._overlay.abort_requested:
            raise QirabotError(
                "aborted by user (ESC held during desktop control)",
                code="user_abort",
            )

    def _pulse_edge_glow(self, adapter: DeviceAdapter) -> None:
        """A call is about to inject REAL mouse/keyboard input outside an
        ai() run: flash the "being controlled" edge glow.

        Debounced in Overlay so scripted bursts (click, click, type…) read
        as one lit stretch; no-op for remote-protocol adapters and while an
        ai() run owns the glow. Best-effort like everything overlay.
        """
        if self._overlay is not None and getattr(adapter, "controls_user_input", False):
            try:
                self._overlay.edge_pulse()
            except Exception:
                pass

    def _execute_action(self, adapter: DeviceAdapter, resp_action: dict[str, Any]) -> None:
        action_type = resp_action.get("actionType", "")
        params = resp_action.get("params", {})
        self._pulse_edge_glow(adapter)
        adapter.execute(action_type, params)

    def fail(self, error_message: str = "") -> None:
        """Record a client-side failure for the run's local bookkeeping.

        Use this when the run is aborted by an error on the client (e.g. your
        script catches an exception) and you want the run recorded as failed
        regardless of the last command's outcome. Idempotent; a subsequent
        close() cannot override it.
        """
        if self._terminalized:
            return
        self._terminalized = True
        # An explicit fail() message wins over whatever the last ai() error
        # was — the caller is being deliberate.
        if error_message:
            self._last_ai_error = error_message
        logger.info("run marked failed%s", f": {error_message}" if error_message else "")

    def cancel(self, reason: str = "") -> None:
        """Record a deliberate client-side abort (e.g. Ctrl+C) so the run is
        treated as cancelled rather than failed or, worse, completed.

        Shares fail()'s terminalized guard so it is idempotent and a later
        close() cannot override it.
        """
        if self._terminalized:
            return
        self._terminalized = True
        logger.info("run cancelled%s", f": {reason}" if reason else "")

    def clear_user_abort(self) -> None:
        """Re-allow ai() runs after a user abort (ESC hold / mouse-to-corner).

        An abort latches: every later :meth:`ai` on this client raises
        ``user_abort`` immediately, so a ``try/except`` around one run can't
        re-take the machine the user just reclaimed. Call this only when
        continuing is a deliberate decision (e.g. after prompting the person
        at the machine). Single-step calls (:meth:`click`, :meth:`press_key`,
        …) are never blocked — they are the script's own explicit actions,
        e.g. cleanup after the abort.

        Note: the task's server-side terminal state was already recorded as
        ``cancelled`` at the moment of the abort; clearing does not undo that.
        """
        self._user_aborted = False
        if self._overlay is not None:
            self._overlay._abort_event.clear()

    def report(self, path: str | None = None) -> Path | None:
        """Write the run report HTML now and return its path.

        Auto-called on :meth:`close` when ``report=True``; call manually only to
        force a custom location or an early snapshot. Returns ``None`` when there
        is nothing to report.
        """
        out = Path(path) if path else (self._report_dir / "report.html")
        return self._write_report(out)

    def _write_report(self, out: Path | None = None) -> Path | None:
        if not self._report or not self._log:
            return None
        from qirabot import report as _report

        out = out or (self._report_dir / "report.html")
        mp4 = self._report_dir / "recording.mp4"
        recording = "recording.mp4" if (mp4.exists() and mp4.stat().st_size > 0) else ""
        still_recording = self._recorder is not None and self._recorder.active
        record_error = ""
        if self._record and not recording and not still_recording:
            record_error = (
                "Recording was requested but not produced — is ffmpeg installed? "
                "(see recording.ffmpeg.log)"
            )
        try:
            _report.write_html(
                self._log,
                out,
                title=self._task_name or "",
                task_id=self._task_id or "",
                outcomes=self._section_outcomes,
                section_errors=self._section_errors,
                recording=recording,
                recording_start=self._record_started_ts if recording else 0.0,
                record_error=record_error,
                # total_steps drives the stats line's headline count; with the
                # synthetic entries gone and local steps synced, len(_log)
                # matches the server's step count.
                stats={**self._stats, "total_steps": len(self._log)},
                model=self._backend.model_label,
            )
            logger.info("report written: %s", out)
            return out
        except Exception:
            logger.debug("failed to write report", exc_info=True)
            return None

    def close(self) -> None:
        """Clean up all resources."""
        if self._closed:
            return
        self._closed = True
        # Take the progress window down first, leaving the final ✓/✗ text up
        # briefly — without the linger the outcome would flash for only the
        # milliseconds between ai() returning and close() running.
        if self._overlay is not None:
            self._overlay.close(linger=1.5)
        # The report is the primary artifact of an aborted run, so guarantee it
        # even if the user mashes Ctrl+C during shutdown: SIGINT is suppressed
        # for this whole block (recording finalize + report write). A plain
        # try/except can't promise this — each Ctrl+C raises a fresh
        # KeyboardInterrupt at an arbitrary point, including inside the write.
        # Worst case ffmpeg is slow to finalize and Ctrl+C is unresponsive for a
        # few seconds (stop_recording is bounded by its own timeouts); normal
        # finalize is sub-second. The try/except blocks below are the fallback
        # for the non-main-thread case where suppression is a no-op.
        with _suppress_sigint():
            # Finalize any in-progress screen recording first so the mp4 is
            # complete on disk (moov atom flushed) when _write_report scans
            # report_dir for it.
            if self._recorder is not None:
                try:
                    self.stop_recording()
                except BaseException:
                    logger.debug("recording teardown interrupted", exc_info=True)
            # Emit the run report before tearing down. Runs on normal exit,
            # exception (via __exit__), and atexit.
            try:
                self._write_report()
            except BaseException:
                logger.debug("report write interrupted", exc_info=True)
        self._terminalized = True
        # Let adapters unhook framework listeners (e.g. Playwright's "page"
        # event) before we tear down the contexts they're attached to. Several
        # cache keys can map to one adapter, so de-dup by identity.
        seen: set[int] = set()
        for adapter in self._adapters.values():
            if id(adapter) in seen:
                continue
            seen.add(id(adapter))
            # Backstop for scripted holds: release any input left held by
            # mouse_down/key_down before tearing the adapter down.
            try:
                adapter.release_all_inputs()
            except Exception:
                pass
            try:
                adapter.close()
            except Exception:
                pass
        self._adapters.clear()
        for page in self._cdp_pages:
            try:
                page.close()
            except Exception:
                pass
        self._cdp_pages.clear()
        for pw in self._pw_instances:
            try:
                pw.stop()
            except Exception:
                pass
        self._pw_instances.clear()
        try:
            self._backend.close()
        except Exception:
            pass
        atexit.unregister(self.close)

    def __enter__(self) -> Qirabot:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        # An exception leaving the with-block means the run aborted: report it
        # instead of letting close() complete it as a success. A KeyboardInterrupt
        # (Ctrl+C) is a deliberate cancel, not a failure.
        if isinstance(exc_val, KeyboardInterrupt):
            self.cancel("aborted by user")
        elif exc_val is not None:
            self.fail(str(exc_val))
        self.close()


def _env_truthy(value: str) -> bool:
    """Parse a boolean-ish env var value (``1``/``true``/``yes``/``on``)."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _extract_coords(params: dict[str, Any] | None) -> tuple[float, float] | None:
    if not params:
        return None
    x = params.get("x")
    y = params.get("y")
    if x is not None and y is not None:
        return (float(x), float(y))
    return None


def _extract_end_coords(
    params: dict[str, Any] | None,
) -> tuple[float, float] | None:
    """Drag's terminal point; used together with ``_extract_coords`` (= start)
    so the report can draw the full start→end path, not just the anchor."""
    if not params:
        return None
    x = params.get("end_x")
    y = params.get("end_y")
    if x is not None and y is not None:
        return (float(x), float(y))
    return None


def _render_step_images(
    data: bytes,
    coords: tuple[float, float] | None,
    config: ScreenshotConfig | None = None,
    *,
    end_coords: tuple[float, float] | None = None,
    coord_scale: float = 1.0,
    thumb_max_edge: int = 800,
    thumb_quality: int = 60,
) -> tuple[bytes, str]:
    """Decode the screenshot once → (full-res encoded bytes, thumbnail data URI).

    Annotates a crosshair at ``coords`` when given and ``config.annotate`` is on;
    otherwise the full-res output is just the source re-encoded in the configured
    format. ``end_coords`` is drag's terminal point — when set, a line + arrow is
    drawn from ``coords`` to it and a hollow ring marks the end. ``coord_scale``
    maps the model's coordinate space onto the screenshot pixel space — 1.0
    everywhere except Appium iOS, where coords arrive in logical points and the
    screenshot is at physical Retina pixels. The thumbnail is always a downscaled
    JPEG embedded as a data URI so the HTML report stays self-contained.
    """
    import base64
    import math

    from PIL import Image, ImageDraw

    cfg = config or ScreenshotConfig()
    img: Image.Image = Image.open(io.BytesIO(data))

    if coords is not None and cfg.annotate:
        img = img.convert("RGBA")
        draw = ImageDraw.Draw(img)
        color = (255, 0, 0, 255)
        short_side = min(img.width, img.height)
        radius = max(4, round(short_side * 0.015))
        line_len = int(radius * 1.5)
        width = 3 if short_side > 2000 else 2
        gap = radius + 2
        cx, cy = int(coords[0] * coord_scale), int(coords[1] * coord_scale)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=color, width=width,
        )
        draw.line([(cx - gap - line_len, cy), (cx - gap, cy)], fill=color, width=width)
        draw.line([(cx + gap, cy), (cx + gap + line_len, cy)], fill=color, width=width)
        draw.line([(cx, cy - gap - line_len), (cx, cy - gap)], fill=color, width=width)
        draw.line([(cx, cy + gap), (cx, cy + gap + line_len)], fill=color, width=width)

        if end_coords is not None:
            ex, ey = int(end_coords[0] * coord_scale), int(end_coords[1] * coord_scale)
            dx, dy = ex - cx, ey - cy
            dist = math.hypot(dx, dy)
            # Skip degenerate drags (start == end) — a zero-length arrow would
            # just draw an artifact on top of the start cross.
            if dist >= 1:
                ux, uy = dx / dist, dy / dist
                # Stop the shaft outside the end ring so they don't overlap.
                sx = cx + int(ux * (radius + gap))
                sy = cy + int(uy * (radius + gap))
                tx = ex - int(ux * (radius + gap))
                ty = ey - int(uy * (radius + gap))
                draw.line([(sx, sy), (tx, ty)], fill=color, width=width)
                # Arrowhead: two short segments rotated ±25° back from the tip.
                head_len = max(8, radius * 2)
                angle = math.atan2(uy, ux)
                for offset in (math.radians(150), math.radians(-150)):
                    hx = tx + int(math.cos(angle + offset) * head_len)
                    hy = ty + int(math.sin(angle + offset) * head_len)
                    draw.line([(tx, ty), (hx, hy)], fill=color, width=width)
                # Hollow end ring, same radius as the start cross's circle so the
                # two endpoints read as a matched pair.
                draw.ellipse(
                    [ex - radius, ey - radius, ex + radius, ey + radius],
                    outline=color, width=width,
                )

    # Full-res output in the configured format (jpeg has no alpha channel, so
    # flatten RGBA → RGB first).
    full_buf = io.BytesIO()
    if cfg.format == "jpeg":
        img.convert("RGB").save(full_buf, format="JPEG", quality=cfg.quality)
    else:
        img.save(full_buf, format="PNG")
    full_bytes = full_buf.getvalue()

    # Thumbnail derived from the same in-memory image — no second decode.
    thumb_img = img if img.mode == "RGB" else img.convert("RGB")
    longest = max(thumb_img.width, thumb_img.height)
    if longest > thumb_max_edge:
        scale = thumb_max_edge / longest
        thumb_img = thumb_img.resize(
            (max(1, round(thumb_img.width * scale)), max(1, round(thumb_img.height * scale)))
        )
    thumb_buf = io.BytesIO()
    thumb_img.save(thumb_buf, format="JPEG", quality=thumb_quality)
    thumb_b64 = "data:image/jpeg;base64," + base64.b64encode(thumb_buf.getvalue()).decode("ascii")

    return full_bytes, thumb_b64
