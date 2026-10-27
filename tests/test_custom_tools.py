"""Custom tools: definition building, run registration, dispatch, feedback.

The loop tests cover the CLIENT pipeline only — what gets registered on
start_ai and how tool results feed back into the next step. Engine-side
validation/behavior is covered in tests/engine/test_local_backend.py.
"""

import pytest

from qirabot import Qirabot
from qirabot._tools import build_tool_defs
from qirabot.adapters.base import DeviceAdapter, DeviceInfo


def gm_command(command: str) -> str:
    """Send a GM command and return the backend's reply."""
    return f"executed {command}"


class _FakeAdapter(DeviceAdapter):
    def __init__(self):
        pass

    def screenshot(self, config=None):
        return b"img"

    def click(self, x, y):
        pass

    def double_click(self, x, y):
        pass

    def type_text(self, x, y, text):
        pass

    def press_key(self, key):
        pass

    def scroll(self, x, y, direction, distance):
        pass

    def device_info(self):
        return DeviceInfo(platform="test", width=100, height=100)


class TestBuildToolDefs:
    def test_introspects_callable(self):
        def add_energy(amount: int, reason_code: str = "test") -> str:
            """Grant energy to the current account."""
            return ""

        defs, handlers = build_tool_defs([add_energy])
        assert defs == [{
            "name": "add_energy",
            "description": "Grant energy to the current account.",
            "parameters": {
                "properties": {
                    "amount": {"type": "integer"},
                    "reason_code": {"type": "string"},
                },
                "required": ["amount"],
            },
        }]
        assert handlers["add_energy"] is add_energy

    def test_no_params_function(self):
        def refresh_cache():
            """Refresh the server-side cache."""

        defs, _ = build_tool_defs([refresh_cache])
        assert "parameters" not in defs[0]

    def test_dict_form_carries_handler(self):
        def handler(command: str) -> str:
            return "ok"

        defs, handlers = build_tool_defs([{
            "name": "gm_exec",
            "description": "Run a GM command.",
            "parameters": {
                "properties": {"command": {"type": "string", "description": "the GM command"}},
                "required": ["command"],
            },
            "handler": handler,
        }])
        assert defs[0]["name"] == "gm_exec"
        assert "handler" not in defs[0], "handler must be stripped from the wire definition"
        assert handlers["gm_exec"] is handler

    @pytest.mark.parametrize(
        ("tools", "match"),
        [
            ([lambda x: x], "lambdas"),
            ([{"name": "x", "description": "d"}], "handler"),
            ([42], "callables or dicts"),
            ([gm_command, gm_command], "duplicate"),
        ],
    )
    def test_rejections(self, tools, match):
        with pytest.raises(ValueError, match=match):
            build_tool_defs(tools)

    def test_missing_docstring_rejected(self):
        def undocumented(x: int) -> int:
            return x

        with pytest.raises(ValueError, match="docstring"):
            build_tool_defs([undocumented])

    def test_bad_name_rejected(self):
        def BadName():
            """Doc."""

        with pytest.raises(ValueError, match="must match"):
            build_tool_defs([BadName])

    def test_var_args_rejected(self):
        def spread(*args):
            """Doc."""

        with pytest.raises(ValueError, match="explicit parameters"):
            build_tool_defs([spread])


class _ToolLoopHarness:
    """Drives the ai() loop against scripted FakeBackend responses.

    The registration passed to the engine is read back from the FakeBackend's
    ``start_calls``; per-step feedback from ``requests``/:attr:`bodies`. The
    adapter must never execute a custom tool step, so ``_execute_action`` is
    rigged to raise.
    """

    def __init__(self, responses, *, report=False):
        self.bot = Qirabot(report=report)
        self.bot._get_adapter = lambda target: _FakeAdapter()
        self.bot._execute_action = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("adapter must not execute custom tool steps")
        )
        self.bot._backend.results.extend(responses)

    @property
    def bodies(self):
        return [request for _, request in self.bot._backend.requests]


DONE = {
    "success": True, "finished": True, "actionType": "done",
    "params": {"result": "done", "success": True}, "output": "done",
}


def _tool_step(name="gm_command", params=None):
    return {
        "success": True, "finished": False,
        "actionType": name, "params": params or {"command": "add_energy 100"},
    }


class TestAiLoopCustomTools:
    def test_run_registers_tools_once(self):
        h = _ToolLoopHarness([_tool_step(), DONE])
        h.bot.ai(object(), "do it", max_steps=3, custom_tools=[gm_command], exclude_tools=["scroll"])
        h.bot.close()

        # Tools/excludes ride on the run registration, not on any step.
        (start,) = h.bot._backend.start_calls
        assert start["custom_tools"][0]["name"] == "gm_command"
        assert start["exclude_tools"] == ["scroll"]

    def test_no_tools_registers_nothing(self):
        h = _ToolLoopHarness([DONE])
        h.bot.ai(object(), "do it", max_steps=3)
        h.bot.close()

        (start,) = h.bot._backend.start_calls
        assert start["custom_tools"] == []
        assert start["exclude_tools"] == []

    def test_dispatch_and_result_feedback(self):
        h = _ToolLoopHarness([_tool_step(), DONE])
        h.bot.ai(object(), "do it", max_steps=3, custom_tools=[gm_command])
        h.bot.close()

        # The handler ran (adapter execute would have raised) and its return
        # value came back as the next step's action_result.
        assert h.bodies[1]["action_result"] == "executed add_energy 100"

    def test_none_return_reports_ok(self):
        calls = []

        def fire_event(name: str):
            """Fire a server event."""
            calls.append(name)

        h = _ToolLoopHarness([
            _tool_step("fire_event", {"name": "login"}), DONE,
        ])
        h.bot.ai(object(), "do it", max_steps=3, custom_tools=[fire_event])
        h.bot.close()

        assert calls == ["login"]
        assert h.bodies[1]["action_result"] == "ok", "None return must map to 'ok', not 'None'"

    def test_handler_exception_feeds_error_back(self):
        def gm_broken(command: str) -> str:
            """Send a GM command."""
            raise RuntimeError("GM backend unreachable")

        h = _ToolLoopHarness([
            _tool_step("gm_broken"), DONE,
        ])
        h.bot.ai(object(), "do it", max_steps=3, custom_tools=[gm_broken])
        h.bot.close()

        assert h.bodies[1]["action_result"].startswith("ERROR:")
        assert "GM backend unreachable" in h.bodies[1]["action_result"]

    def test_invalid_tools_raise_before_any_step(self):
        h = _ToolLoopHarness([DONE])
        with pytest.raises(ValueError, match="lambdas"):
            h.bot.ai(object(), "do it", max_steps=3, custom_tools=[lambda x: x])
        h.bot.close()
        assert h.bodies == []  # nothing reached the engine


def _real_engine_bot(*responses):
    """A bot on the REAL engine with only the model endpoint scripted: for
    behavior decided by engine-side schema handling, which FakeBackend skips."""
    from qirabot.engine.local_backend import LocalBackend
    from qirabot.engine.providers.base import ChatResponse
    from qirabot.engine.types import TokenUsage, ToolCall

    class _Provider:
        def __init__(self):
            self.requests = []
            self.responses = [
                ChatResponse(
                    tool_calls=[ToolCall(id=name, name=name, args=args)],
                    token_usage=TokenUsage(),
                    model_used="fake-model",
                )
                for name, args in responses
            ]

        def chat(self, request, timeout):
            self.requests.append(request)
            return self.responses.pop(0)

    provider = _Provider()
    bot = Qirabot(report=False)
    bot._backend = LocalBackend(model="gemini-vertex/gemini-test", provider=provider)
    bot._get_adapter = lambda target: _FakeAdapter()
    return bot, provider


class TestRealEngineCustomTools:
    def test_reason_param_reaches_handler(self):
        # Customer report: a tool parameter named `reason` (also the engine's
        # step-reasoning field) failed the run with
        # 'tool "report_unreachable": property name "reason" is reserved'.
        calls = []

        def report_unreachable(reason: str) -> str:
            """Report that the target cannot be reached, and why."""
            calls.append(reason)
            return "reported"

        bot, provider = _real_engine_bot(
            ("report_unreachable", {"step_reason": "页面一直打不开", "reason": "门锁了"}),
            ("done", {"reason": "已上报", "success": False, "result": "目标不可达"}),
        )
        result = bot.ai(object(), "打开目标页面", max_steps=3, custom_tools=[report_unreachable])
        bot.close()

        assert calls == ["门锁了"]
        assert result.steps[0].params == {"reason": "门锁了"}
        assert result.steps[0].decision == "页面一直打不开"
        assert result.status == "goal_failed"
        sent = next(t for t in provider.requests[0].tools if t.name == "report_unreachable")
        assert sent.parameters["required"] == ["reason", "step_reason"]
