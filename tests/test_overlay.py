"""Tests for the on-screen progress overlay (qirabot.overlay).

The GUI itself can't run headless; these tests cover the parent side — the
process plumbing, the wire protocol, the no-op degradation guarantees — with
a fake Popen, plus the helper's unsupported-platform exit code for real.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from qirabot.client import StepResult
from qirabot.overlay import Overlay, _format_step


class _FakeStdin:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.data = b""
        self.closed = False

    def write(self, b: bytes) -> int:
        if self.fail:
            raise BrokenPipeError("helper died")
        self.data += b
        return len(b)

    def flush(self) -> None:
        if self.fail:
            raise BrokenPipeError("helper died")

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self, fail: bool = False, stdout: bytes = b""):
        import io

        self.stdin = _FakeStdin(fail=fail)
        self.stdout = io.BytesIO(stdout)  # helper->parent event stream
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


@pytest.fixture
def fake_spawn(monkeypatch):
    """Force a supported platform and capture the helper spawn."""
    monkeypatch.setattr(sys, "platform", "darwin")
    procs: list[_FakeProc] = []

    def popen(cmd, **kwargs):
        assert cmd[1:] == ["-m", "qirabot._overlay_helper"]
        proc = _FakeProc()
        procs.append(proc)
        return proc

    monkeypatch.setattr("qirabot.overlay.subprocess.Popen", popen)
    return procs


def _sent_lines(proc: _FakeProc) -> list[dict]:
    return [json.loads(line) for line in proc.stdin.data.decode().splitlines()]


def _step(**kwargs) -> StepResult:
    defaults = dict(step=3, action_type="click")
    defaults.update(kwargs)
    return StepResult(**defaults)


def test_set_text_sends_one_json_line(fake_spawn):
    ov = Overlay()
    ov.set_text("hello 你好")
    assert _sent_lines(fake_spawn[0]) == [{"text": "hello 你好"}]
    # The wire must stay pure ASCII (\uXXXX escapes): the helper's stdin
    # decoding follows the OS locale (GBK on Chinese Windows), and raw UTF-8
    # on the pipe came out as mojibake there.
    assert fake_spawn[0].stdin.data.isascii()


def test_start_is_lazy_and_idempotent(fake_spawn):
    ov = Overlay()
    assert fake_spawn == []
    ov.set_text("a")
    ov.set_text("b")
    ov.start()
    assert len(fake_spawn) == 1


def test_step_renders_action_and_decision(fake_spawn):
    ov = Overlay()
    ov.step(_step(params={"locate": "Login button"}, decision="need to sign in"))
    (msg,) = _sent_lines(fake_spawn[0])
    assert msg["text"] == 'step 3 · click · "Login button"\nneed to sign in'


def test_step_with_total_shows_denominator(fake_spawn):
    ov = Overlay()
    ov.step(_step(), total=20)
    (msg,) = _sent_lines(fake_spawn[0])
    assert msg["text"].startswith("step 3/20 · click")


def test_begin_sets_title_state_and_clears_body(fake_spawn):
    ov = Overlay()
    ov.begin("打开备忘录并新建一条笔记")
    (msg,) = _sent_lines(fake_spawn[0])
    assert msg == {
        "title": "打开备忘录并新建一条笔记",
        "state": "run",
        "text": "",
        "edge": False,
    }


def test_begin_edge_glow_rides_the_run_message(fake_spawn):
    ov = Overlay()
    ov.begin("drive the desktop", edge_glow=True)
    (msg,) = _sent_lines(fake_spawn[0])
    assert msg["edge"] is True
    # The kill-switch hint pill text rides along — an invisible abort key
    # is an abort key nobody uses.
    assert "ESC" in msg["hint"]


def test_begin_clips_long_instruction(fake_spawn):
    ov = Overlay()
    ov.begin("x" * 500)
    (msg,) = _sent_lines(fake_spawn[0])
    assert len(msg["title"]) == 80 and msg["title"].endswith("…")


def test_close_sends_command_and_closes_stdin(fake_spawn):
    ov = Overlay()
    ov.set_text("x")
    ov.close()
    proc = fake_spawn[0]
    assert _sent_lines(proc)[-1] == {"cmd": "close", "linger": 0.0}
    assert proc.stdin.closed
    # Close is idempotent and post-close sends must not respawn silently
    # against a dead handle.
    ov.close()


def test_unsupported_platform_is_noop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    spawned = []
    monkeypatch.setattr(
        "qirabot.overlay.subprocess.Popen",
        lambda *a, **k: spawned.append(a),
    )
    ov = Overlay()
    ov.start()
    ov.set_text("ignored")
    ov.step(_step())
    ov.close()
    assert spawned == []


def test_helper_death_degrades_to_noop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    procs: list[_FakeProc] = []

    def popen(cmd, **kwargs):
        proc = _FakeProc(fail=True)
        procs.append(proc)
        return proc

    monkeypatch.setattr("qirabot.overlay.subprocess.Popen", popen)
    ov = Overlay()
    ov.set_text("first write fails")
    ov.set_text("and must not respawn")
    ov.close()
    assert len(procs) == 1
    # The pipe must be closed on failure, or the buffered writer would
    # re-flush at interpreter shutdown and print an "Exception ignored"
    # BrokenPipeError traceback.
    assert procs[0].stdin.closed


def test_spawn_failure_degrades_to_noop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    def popen(cmd, **kwargs):
        calls.append(cmd)
        raise OSError("no python")

    monkeypatch.setattr("qirabot.overlay.subprocess.Popen", popen)
    ov = Overlay()
    ov.set_text("a")
    ov.set_text("b")
    assert len(calls) == 1  # failed once, then stopped trying


def test_finish_shows_outcome(fake_spawn):
    ov = Overlay()
    ov.finish(True, "Note created")
    ov.finish(False, "")
    lines = _sent_lines(fake_spawn[0])
    # edge always rides along as False: control has ended, and an unpaired
    # begin(edge_glow=True) must never leave the glow breathing forever.
    assert lines[0] == {"state": "ok", "text": "Note created", "edge": False}
    # No message: state only, so the last step stays visible in the body.
    assert lines[1] == {"state": "fail", "edge": False}


def test_close_forwards_linger(fake_spawn):
    ov = Overlay()
    ov.set_text("x")
    ov.close(linger=1.5)
    assert _sent_lines(fake_spawn[0])[-1] == {"cmd": "close", "linger": 1.5}


def test_wrap_chains_user_callback(fake_spawn):
    ov = Overlay()
    seen = []
    chained = ov.wrap(seen.append)
    step = _step()
    chained(step)
    assert seen == [step]
    assert _sent_lines(fake_spawn[0])  # overlay was fed too


def test_wrap_without_user_callback_is_step(fake_spawn):
    ov = Overlay()
    assert ov.wrap(None) == ov.step


def test_context_manager_spawns_and_closes(fake_spawn):
    with Overlay() as ov:
        ov.set_text("running")
    proc = fake_spawn[0]
    assert _sent_lines(proc)[-1] == {"cmd": "close", "linger": 0.0}


def test_format_step_clips_every_field_independently():
    # Every unbounded field at once: the locate description, the typed
    # text, and the decision must each be clipped on its own, so no single
    # field can crowd the others out of the fixed-size window.
    text = _format_step(
        _step(params={"locate": "L" * 300, "text": "T" * 300}, decision="d" * 500)
    )
    head, decision = text.split("\n")
    assert len(head) <= 70
    assert '"L' in head and "← \"T" in head  # both params survived clipping
    assert len(decision) <= 160 and decision.endswith("…")


def test_format_step_clips_head_and_decision():
    text = _format_step(_step(params={"locate": "x" * 200}, decision="d" * 200))
    head, decision = text.split("\n")
    assert len(head) <= 70 and head.endswith('…"')
    assert len(decision) <= 160 and decision.endswith("…")


def test_format_step_type_text_and_scroll():
    assert (
        _format_step(_step(action_type="type", params={"text": "hi"}))
        == 'step 3 · type · ← "hi"'
    )
    assert (
        _format_step(
            _step(action_type="scroll", params={"direction": "down", "amount": 300})
        )
        == "step 3 · scroll · down 300"
    )


def test_fmt_elapsed():
    from qirabot._overlay_helper import _fmt_elapsed

    assert _fmt_elapsed(7) == "0:07"
    assert _fmt_elapsed(131) == "2:11"
    assert _fmt_elapsed(3661) == "1:01:01"


def test_edge_alpha_breathes_within_bounds():
    from qirabot._overlay_helper import (
        _EDGE_ALPHA_HI,
        _EDGE_ALPHA_LO,
        _EDGE_PERIOD,
        _EDGE_TICK_MS,
        _edge_alpha,
    )

    ticks_per_period = int(_EDGE_PERIOD * 1000 / _EDGE_TICK_MS)
    values = [_edge_alpha(t) for t in range(3 * ticks_per_period)]
    assert all(_EDGE_ALPHA_LO <= v <= _EDGE_ALPHA_HI for v in values)
    # It actually breathes: starts at the floor, reaches (near) the ceiling.
    assert _edge_alpha(0) == pytest.approx(_EDGE_ALPHA_LO)
    assert max(values) == pytest.approx(_EDGE_ALPHA_HI, abs=0.01)


def test_client_edge_glow_follows_adapter_input_control(fake_spawn):
    # Desktop backends (real mouse/keyboard) light the glow; remote-protocol
    # backends must not — the "hands off" signal would be a lie there.
    from qirabot.client import Qirabot, RunResult

    class _FakeAdapter:
        controls_user_input = True

        def release_all_inputs(self):
            pass

    adapter = _FakeAdapter()
    bot = Qirabot(overlay=True)
    bot._get_adapter = lambda target: adapter
    bot._ai_loop = lambda *a, **k: RunResult(success=True, output="done")

    bot.ai(object(), "drive the desktop")
    adapter.controls_user_input = False
    bot.ai(object(), "drive a browser")

    lines = _sent_lines(fake_spawn[0])
    runs = [m for m in lines if m.get("state") == "run"]
    ends = [m for m in lines if m.get("state") in ("ok", "fail")]
    assert [m["edge"] for m in runs] == [True, False]
    assert all(m["edge"] is False for m in ends)


def test_esc_abort_event_sets_flag_and_begin_resets(monkeypatch):
    import time as _time

    monkeypatch.setattr(sys, "platform", "darwin")
    proc = _FakeProc(stdout=b'not json\n{"event": "abort"}\n')
    monkeypatch.setattr("qirabot.overlay.subprocess.Popen", lambda *a, **k: proc)
    ov = Overlay()
    ov.start()
    deadline = _time.time() + 2.0
    while _time.time() < deadline and not ov.abort_requested:
        _time.sleep(0.005)
    assert ov.abort_requested
    ov.begin("next run")  # a new run must start unaborted
    assert not ov.abort_requested


def test_long_press_state_machine():
    from qirabot._overlay_helper import _LongPress

    lp = _LongPress(1.0)
    assert not lp.expired(0.0)  # nothing pressed
    lp.down(10.0)
    lp.down(10.5)  # OS key-repeat: must NOT reset the clock
    assert not lp.expired(10.9)
    assert lp.expired(11.0)  # held past the threshold
    assert not lp.expired(12.0)  # latched: one fire per press
    lp.down(20.0)
    lp.up()
    assert not lp.expired(25.0)  # released in time: a tap, not a hold


def test_client_aborts_between_steps_on_esc_hold(fake_spawn):
    # The reader thread latches abort while a step runs; the loop must end
    # the run at the next step boundary instead of injecting more input.
    from qirabot.client import Qirabot
    from qirabot.exceptions import QirabotError

    class _FakeAdapter:
        controls_user_input = True

        def release_all_inputs(self):
            pass

        def screenshot(self, config=None):
            return b"png"

        def device_info(self):
            from qirabot.adapters.base import DeviceInfo

            return DeviceInfo(platform="desktop", width=10, height=10)

        def annotation_scale(self):
            return 1.0

    executed = []
    bot = Qirabot(overlay=True)
    bot._get_adapter = lambda target: _FakeAdapter()
    bot._record_step = lambda *a, **k: None
    bot._execute_action = lambda *a, **k: executed.append(1)
    assert bot._overlay is not None

    def decide():
        # ESC held while the model was thinking
        bot._overlay._abort_event.set()
        return {"success": True, "finished": False, "actionType": "wait", "params": {}}

    bot._backend.results.append(decide)
    with pytest.raises(QirabotError) as excinfo:
        bot.ai(object(), "drive the desktop", max_steps=5)
    assert getattr(excinfo.value, "code", "") == "user_abort"
    # The action the model returned AFTER the abort must never be injected:
    # "I hit the kill switch and it clicked once more" is not acceptable.
    assert executed == []
    # Recorded as a user cancellation, not a bot failure.
    assert bot._last_ai_status == "cancelled"
    # The overlay shows the failed ending, glow off.
    last = _sent_lines(fake_spawn[0])[-1]
    assert last["state"] == "fail" and last["edge"] is False
    # Sticky: a script catching the exception must not re-take the machine
    # with its next bot.ai() — it raises up front, no glow, no injection.
    posts_before = len(_sent_lines(fake_spawn[0]))
    with pytest.raises(QirabotError) as second:
        bot.ai(object(), "next stage of the same script")
    assert getattr(second.value, "code", "") == "user_abort"
    assert executed == []
    assert len(_sent_lines(fake_spawn[0])) == posts_before  # overlay untouched


def test_clear_user_abort_re_allows_runs(fake_spawn):
    from qirabot.client import Qirabot, RunResult

    class _FakeAdapter:
        controls_user_input = True

        def release_all_inputs(self):
            pass

    loops = []
    bot = Qirabot(overlay=True)
    bot._get_adapter = lambda target: _FakeAdapter()
    bot._ai_loop = lambda *a, **k: loops.append(1) or RunResult(success=True, output="ok")
    bot._user_aborted = True  # as latched by an earlier ESC abort

    from qirabot.exceptions import QirabotError

    with pytest.raises(QirabotError):
        bot.ai(object(), "blocked")
    assert loops == [] and fake_spawn == []  # helper never even spawned

    bot.clear_user_abort()  # the script's deliberate decision to continue
    bot.ai(object(), "resumed")
    assert loops == [1]
    assert any(m.get("state") == "run" for m in _sent_lines(fake_spawn[0]))


def test_edge_pulse_debounces_to_one_lit_stretch(fake_spawn):
    import time as _time

    ov = Overlay()
    ov.edge_pulse(linger=0.05)
    ov.edge_pulse(linger=0.05)  # burst: merges into the same stretch
    deadline = _time.time() + 2.0
    while _time.time() < deadline:
        lines = _sent_lines(fake_spawn[0])
        if any(m.get("edge") is False for m in lines):
            break
        _time.sleep(0.005)
    lines = _sent_lines(fake_spawn[0])
    assert [m["edge"] for m in lines] == [True, True, False]  # ONE fade-out
    assert all("ESC" in m["hint"] for m in lines if m["edge"])


def test_edge_pulse_yields_to_a_running_ai_glow(fake_spawn):
    ov = Overlay()
    ov.begin("drive", edge_glow=True)
    ov.edge_pulse(linger=0.01)  # the run owns the glow: no-op
    ov.finish(True, "done")
    lines = _sent_lines(fake_spawn[0])
    # begin's edge:true and finish's edge:false only — no pulse traffic that
    # could switch the glow off mid-run.
    assert [m.get("edge") for m in lines] == [True, False]


def test_single_step_call_pulses_the_glow(fake_spawn):
    # bot.press_key() etc. on a desktop backend inject real input before any
    # ai() runs — the glow must cover them too, not just ai().
    from qirabot.client import Qirabot

    class _FakeAdapter:
        controls_user_input = True

        def execute(self, action_type, params):
            pass

        def screenshot(self, config=None):
            return b""

        @property
        def current_target(self):
            return None

    bot = Qirabot(overlay=True)
    bot._get_adapter = lambda target: _FakeAdapter()
    bot.press_key(object(), "Enter")
    lines = _sent_lines(fake_spawn[0])
    assert any(m.get("edge") is True for m in lines)


@pytest.mark.skipif(
    sys.platform in ("darwin", "win32"),
    reason="on GUI platforms the helper would open a real window",
)
def test_helper_exits_3_on_unsupported_platform():
    proc = subprocess.run(
        [sys.executable, "-m", "qirabot._overlay_helper"],
        input=b"",
        capture_output=True,
        timeout=30,
    )
    assert proc.returncode == 3
