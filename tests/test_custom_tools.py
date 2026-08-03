"""Custom tools: definition building, wire registration, dispatch, feedback.

The loop tests cover the CLIENT pipeline only — the outbound step-1 ai params
and the registration-echo log behavior. The engine-side echoing itself is
covered in tests/engine/test_local_backend.py.
"""

import logging

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

    Outbound request bodies are read back from the FakeBackend's captured
    ``requests`` via :attr:`bodies`. The adapter must never execute a custom
    tool step, so ``_execute_action`` is rigged to raise.
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


def _tool_step(name="gm_command", params=None, registration=True):
    resp = {
        "success": True, "finished": False,
        "actionType": name, "params": params or {"command": "add_energy 100"},
    }
    if registration:
        resp["tool_registration"] = {"registered": [name], "excluded": []}
    return resp


class TestAiLoopCustomTools:
    def test_first_request_registers_tools(self):
        h = _ToolLoopHarness([_tool_step(), DONE])
        h.bot.ai(object(), "do it", max_steps=3, custom_tools=[gm_command], exclude_tools=["scroll"])
        h.bot.close()

        params = h.bodies[0]["action"]["params"]
        assert params["custom_tools"][0]["name"] == "gm_command"
        assert params["exclude_tools"] == ["scroll"]
        # Continuation steps carry an empty ai envelope — tools ride on step 1 only.
        assert h.bodies[1]["action"] == {"type": "ai", "params": {}}

    def test_no_tools_means_no_keys(self):
        h = _ToolLoopHarness([DONE])
        h.bot.ai(object(), "do it", max_steps=3)
        h.bot.close()

        params = h.bodies[0]["action"]["params"]
        assert "custom_tools" not in params
        assert "exclude_tools" not in params

    def test_dispatch_and_result_feedback(self):
        h = _ToolLoopHarness([_tool_step(), DONE])
        h.bot.ai(object(), "do it", max_steps=3, custom_tools=[gm_command])
        h.bot.close()

        # The handler ran (adapter execute would have raised) and its return
        # value came back as the next request's action_result.
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

    def test_old_server_warning(self, caplog):
        # A successful step-1 response without the tool_registration echo means
        # the backend ignored the tools param.
        h = _ToolLoopHarness([
            {"success": True, "finished": True, "actionType": "done",
             "params": {"result": "done", "success": True}, "output": "done"},
        ])
        with caplog.at_level(logging.WARNING, logger="qirabot"):
            h.bot.ai(object(), "do it", max_steps=3, custom_tools=[gm_command])
        h.bot.close()
        assert any("does not support custom_tools" in r.getMessage() for r in caplog.records)

    def test_no_false_warning_when_echoed(self, caplog):
        done = dict(DONE)
        done["tool_registration"] = {"registered": ["gm_command"], "excluded": []}
        h = _ToolLoopHarness([done])
        with caplog.at_level(logging.INFO, logger="qirabot"):
            h.bot.ai(object(), "do it", max_steps=3, custom_tools=[gm_command])
        h.bot.close()
        assert not any("does not support" in r.getMessage() for r in caplog.records)
        assert any("custom tools registered" in r.getMessage() for r in caplog.records)

    def test_server_warning_logged(self, caplog):
        done = dict(DONE)
        done["tool_registration"] = {"registered": ["gm_command"], "excluded": []}
        done["warning"] = (
            "custom_tools/exclude_tools already registered for this session; "
            "incoming values ignored"
        )
        h = _ToolLoopHarness([done])
        with caplog.at_level(logging.WARNING, logger="qirabot"):
            h.bot.ai(object(), "do it", max_steps=3, custom_tools=[gm_command])
        h.bot.close()
        assert any("already registered" in r.getMessage() for r in caplog.records)
