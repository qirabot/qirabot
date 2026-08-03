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


class FakeBackend:
    """Stands in for qirabot.engine.local_backend.LocalBackend.

    - ``requests``: every act() call as ``(screenshot_bytes, request_dict)``,
      in order — assert outbound request bodies here.
    - ``results``: FIFO queue of responses to hand back. Each item may be a
      response dict, an Exception instance (raised), or a zero-arg callable
      (its return value is used; it may also raise). When the queue is empty
      a default successful ``done`` response is returned.
    """

    model_label = "fake/fake-model"

    def __init__(self, *args, **kwargs):
        self.init_args = args
        self.init_kwargs = kwargs
        self.requests = []
        self.results = []
        self.closed = False

    def act(self, screenshot, request, mime=""):
        self.requests.append((screenshot, request))
        if self.results:
            result = self.results.pop(0)
            if isinstance(result, BaseException):
                raise result
            if callable(result):
                return result()
            return result
        return default_done_response()

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
        _, body = bot._backend.requests[0]     # assert the outbound request

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
