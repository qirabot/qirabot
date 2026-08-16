"""Shared test fixtures.

v3: the decision engine runs in-process (qirabot.engine). Tests never talk to
a real provider — the autouse ``_fake_local_backend`` fixture swaps the
``LocalBackend`` reference *inside qirabot.client* for :class:`FakeBackend`,
so ``Qirabot()`` constructs without GCP ADC credentials and every decision
call is scripted by the test. tests/engine/ constructs the real LocalBackend
directly (with an injected provider) and is unaffected by that patch.
"""

import contextlib

import pytest


def default_done_response():
    """The response FakeBackend returns when nothing is queued: a successful
    terminal 'done' step, so single actions succeed and ai() loops finish in
    one step."""
    return {
        "success": True,
        "actionType": "done",
        "params": {"success": True},
        "finished": True,
        "output": "ok",
    }


def _usage_from(d):
    from qirabot.engine.types import TokenUsage

    return TokenUsage(
        input_tokens=d.get("inputTokens", 0),
        output_tokens=d.get("outputTokens", 0),
        thinking_tokens=d.get("thinkingTokens", 0),
        cache_read_tokens=d.get("cacheReadTokens", 0),
        cache_write_tokens=d.get("cacheWriteTokens", 0),
    )


class _FakeRun:
    """Stands in for qirabot.engine.local_backend.AIRun."""

    def __init__(self, backend):
        self._backend = backend
        self._steps = 0

    def step(self, screenshot, action_result="", device_width=0, device_height=0):
        from qirabot.engine.session import StepError, StepOutcome

        b = self._backend
        b.requests.append(
            (
                screenshot,
                {
                    "type": "ai_step",
                    "action_result": action_result,
                    "width": device_width,
                    "height": device_height,
                },
            )
        )
        d = b._next()
        if isinstance(d, StepOutcome):
            return d
        if d.get("success", True) is False:
            raise StepError(d.get("error", "step failed"), usage=_usage_from(d))
        self._steps += 1
        return StepOutcome(
            action_type=d.get("actionType", ""),
            params=d.get("params") or {},
            decision=d.get("decision", ""),
            output=d.get("output", ""),
            finished=d.get("finished", False),
            step_number=self._steps,
            token_usage=_usage_from(d),
            llm_decision_ms=d.get("llmDecisionDurationMs", 0),
            step_duration_ms=d.get("stepDurationMs", 0),
        )


class FakeBackend:
    """Stands in for qirabot.engine.local_backend.LocalBackend.

    - ``start_calls``: every start_ai() call's kwargs, in order — assert the
      registered instruction/tools/knowledge here.
    - ``requests``: every engine call as ``(screenshot_bytes, record_dict)``,
      in order. ai steps record ``{"type": "ai_step", "action_result", ...}``;
      single actions record their kind plus the query and per-call overrides.
    - ``results``: FIFO queue of scripted results. Each item may be a legacy
      wire-shaped dict (translated to the typed outcome the call expects), an
      Exception instance (raised), or a zero-arg callable (its return value is
      used; it may also raise). When the queue is empty a default successful
      ``done`` response is returned.
    """

    model_label = "fake/fake-model"
    tier_label = ""

    def __init__(self, *args, **kwargs):
        self.init_args = args
        self.init_kwargs = kwargs
        self.start_calls = []
        self.requests = []
        self.results = []
        self.closed = False

    def _next(self):
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, BaseException):
                raise result
            if callable(result):
                return result()
            return result
        return default_done_response()

    def start_ai(
        self,
        instruction,
        *,
        platform,
        max_steps=20,
        language="",
        thinking_level="",
        custom_tools=None,
        exclude_tools=None,
        knowledge="",
    ):
        self.start_calls.append(
            {
                "instruction": instruction,
                "platform": platform,
                "max_steps": max_steps,
                "language": language,
                "thinking_level": thinking_level,
                "custom_tools": custom_tools or [],
                "exclude_tools": exclude_tools or [],
                "knowledge": knowledge,
            }
        )
        return _FakeRun(self)

    def locate(self, screenshot, locate, *, language="", thinking_level=""):
        from qirabot.engine.local_backend import LocateOutcome

        self.requests.append(
            (
                screenshot,
                {
                    "type": "locate",
                    "locate": locate,
                    "thinking_level": thinking_level,
                    "language": language,
                },
            )
        )
        d = self._next()
        if d.get("success", True) is False:
            return LocateOutcome(
                found=False,
                error=d.get("error", "element not found"),
                token_usage=_usage_from(d),
            )
        p = d.get("params") or {}
        return LocateOutcome(
            found=True,
            x=int(p.get("x", 0)),
            y=int(p.get("y", 0)),
            token_usage=_usage_from(d),
        )

    def extract(self, screenshot, instruction, *, platform="", language="", thinking_level=""):
        from qirabot.engine.local_backend import ExtractOutcome

        self.requests.append(
            (
                screenshot,
                {
                    "type": "extract",
                    "instruction": instruction,
                    "thinking_level": thinking_level,
                    "language": language,
                },
            )
        )
        d = self._next()
        if d.get("success", True) is False:
            raise ValueError(d.get("error", "extract failed"))
        return ExtractOutcome(result=d.get("output", ""), token_usage=_usage_from(d))

    def check_condition(
        self, screenshot, condition, *, platform="", language="", thinking_level=""
    ):
        from qirabot.engine.local_backend import ConditionOutcome

        self.requests.append(
            (
                screenshot,
                {
                    "type": "assert",
                    "condition": condition,
                    "thinking_level": thinking_level,
                    "language": language,
                },
            )
        )
        d = self._next()
        if d.get("success", True) is False:
            raise ValueError(d.get("error", "condition check failed"))
        return ConditionOutcome(
            met=d.get("finished", False),
            reasoning=d.get("output", ""),
            token_usage=_usage_from(d),
        )

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _fake_local_backend(monkeypatch):
    """Keep every Qirabot() construction off the real engine.

    The real LocalBackend resolves GCP ADC credentials in __init__, which is
    unavailable (and undesirable) in tests. client.py binds the class at
    import time (``from qirabot.engine.local_backend import LocalBackend``),
    so the patch point is the *client module's* reference. Individual tests
    may re-patch it with their own double.
    """
    monkeypatch.setattr("qirabot.client.LocalBackend", FakeBackend)


@pytest.fixture(autouse=True)
def _isolate_user_config(tmp_path, monkeypatch):
    """Point the user-level config dir at a temp path for every test, and
    scrub Qirabot env vars that change construction behavior (a developer's
    shell may carry QIRA_MODEL / a stale QIRA_API_KEY / report settings)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    for var in (
        "QIRA_API_KEY",
        "QIRA_MODEL",
        "QIRA_MEDIA_RESOLUTION",
        "QIRA_REPORT_DIR",
        "QIRA_RECORD",
        "QIRA_RECORD_WINDOW",
        "QIRA_RECORD_AUDIO",
        "QIRA_RECORD_DEVICE",
        "QIRA_RECORD_MJPEG_URL",
        "QIRA_SETTLE_SECONDS",
        "QIRA_LOCATE_FORMAT",
        "QIRA_ENGINE_TRACE",
        "QIRA_VERTEX_PROJECT",
        "QIRA_VERTEX_LOCATION",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def make_bot(tmp_path):
    """Build a Qirabot wired to a FakeBackend; auto-closed at teardown.

    Usage::

        bot = make_bot()                       # report off by default
        bot._backend.results.append({...})     # queue a decision response
        bot.click(target, "OK")
        _, body = bot._backend.requests[0]     # assert the outbound call

    Keyword args pass straight to Qirabot(); ``report`` defaults to False and
    ``report_dir`` to a tmp path so tests never write ./qira_runs.
    """
    from qirabot.client import Qirabot

    bots = []

    def _make(**kwargs):
        kwargs.setdefault("report", False)
        kwargs.setdefault("report_dir", str(tmp_path / "qira_runs"))
        bot = Qirabot(**kwargs)
        bots.append(bot)
        return bot

    yield _make
    for bot in bots:
        with contextlib.suppress(Exception):
            bot.close()
