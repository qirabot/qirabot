"""Request-level thinking_level override: body construction, fallback
semantics, auto-wait pass-through, bound proxy, and the CLI flag.

v3: request bodies go to the local engine via ``bot._backend.act`` — assert
them on the FakeBackend's captured ``requests`` instead of multipart data.
"""

from unittest.mock import MagicMock

from qirabot.adapters.base import DeviceAdapter, DeviceInfo
from qirabot.bound import _BoundQirabot


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


class TestSingleActionBody:
    """The four quadrants of the per-call vs instance-default fallback on the
    _ai_action_once request body."""

    def _bot(self, make_bot, **kwargs):
        bot = make_bot(**kwargs)
        bot._get_adapter = lambda target: _FakeAdapter()
        return bot

    def _sent_body(self, bot):
        return bot._backend.requests[-1][1]

    def test_both_empty_sends_engine_default(self, make_bot):
        bot = self._bot(make_bot)
        bot.extract("target", "read it")
        assert self._sent_body(bot)["thinking_level"] == ""

    def test_instance_default_applies(self, make_bot):
        bot = self._bot(make_bot, thinking_level="low")
        bot.extract("target", "read it")
        assert self._sent_body(bot)["thinking_level"] == "low"

    def test_per_call_applies(self, make_bot):
        bot = self._bot(make_bot)
        bot.extract("target", "read it", thinking_level="high")
        assert self._sent_body(bot)["thinking_level"] == "high"

    def test_per_call_overrides_instance(self, make_bot):
        bot = self._bot(make_bot, thinking_level="low")
        bot.extract("target", "read it", thinking_level="high")
        assert self._sent_body(bot)["thinking_level"] == "high"


class TestAiLoopBody:
    # The level is registered once on the run and applies to every step; a
    # non-terminal first step proves the run spans two steps under it.
    _CLICK_STEP = {
        "success": True, "finished": False,
        "actionType": "click", "params": {"x": 1, "y": 2},
    }

    def _bot(self, make_bot, **kwargs):
        bot = make_bot(**kwargs)
        bot._get_adapter = lambda target: _FakeAdapter()
        bot._backend.results.append(dict(self._CLICK_STEP))
        return bot

    def test_per_call_applies_to_the_run(self, make_bot):
        bot = self._bot(make_bot)
        bot.ai(object(), "task", max_steps=2, thinking_level="medium")
        (start,) = bot._backend.start_calls
        assert start["thinking_level"] == "medium"
        assert len(bot._backend.requests) == 2  # both steps ran under it

    def test_instance_default_applies_to_the_run(self, make_bot):
        bot = self._bot(make_bot, thinking_level="minimal")
        bot.ai(object(), "task", max_steps=2)
        (start,) = bot._backend.start_calls
        assert start["thinking_level"] == "minimal"

    def test_engine_default_when_unset(self, make_bot):
        bot = self._bot(make_bot)
        bot.ai(object(), "task", max_steps=2)
        (start,) = bot._backend.start_calls
        assert start["thinking_level"] == ""


class TestAutoWaitChain:
    """The auto-wait verify of click(timeout>0) must run at the same thinking
    level as the click itself — both LLM calls belong to one user action."""

    def _bot(self, make_bot):
        bot = make_bot()
        bot._get_adapter = lambda target: _FakeAdapter()
        bot._ai_action = MagicMock(return_value={
            "success": True, "finished": True,
            "actionType": "click", "params": {"x": 1, "y": 2},
        })
        return bot

    def test_click_auto_wait_carries_thinking_level(self, make_bot):
        bot = self._bot(make_bot)
        bot.wait_for = MagicMock()
        bot.click("target", "OK", timeout=5, thinking_level="high")
        assert bot.wait_for.call_args.kwargs["thinking_level"] == "high"
        assert bot._ai_action.call_args.kwargs["thinking_level"] == "high"

    def test_wait_for_passes_to_verify(self, make_bot):
        bot = self._bot(make_bot)
        bot.verify = MagicMock()  # truthy -> met on first poll
        bot.wait_for("target", "cart shows 1 item", timeout=1, thinking_level="medium")
        assert bot.verify.call_args.kwargs["thinking_level"] == "medium"


class TestBoundProxy:
    def test_action_methods_pass_through(self):
        inner = MagicMock()
        bound = _BoundQirabot(inner, target="tgt")
        bound.click("OK", thinking_level="high")
        assert inner.click.call_args.kwargs["thinking_level"] == "high"
        bound.extract("read it", thinking_level="low")
        assert inner.extract.call_args.kwargs["thinking_level"] == "low"
        bound.ai("do it", thinking_level="medium")
        assert inner.ai.call_args.kwargs["thinking_level"] == "medium"
        bound.wait_for("done", thinking_level="minimal")
        assert inner.wait_for.call_args.kwargs["thinking_level"] == "minimal"


class TestCliFlag:
    def test_thinking_level_reaches_make_bot(self, monkeypatch):
        from click.testing import CliRunner

        from qirabot.cli import main

        captured = {}

        def spy_make_bot(ctx, **kwargs):
            captured.update(kwargs)
            return MagicMock(name="bot")

        monkeypatch.setattr(main, "_make_bot", spy_make_bot)
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)

        result = CliRunner().invoke(main.cli, ["browser", "do it", "--thinking-level", "high"])
        assert result.exit_code == 0, result.output
        assert captured["thinking_level"] == "high"

    def test_default_is_empty(self, monkeypatch):
        from click.testing import CliRunner

        from qirabot.cli import main

        captured = {}

        def spy_make_bot(ctx, **kwargs):
            captured.update(kwargs)
            return MagicMock(name="bot")

        monkeypatch.setattr(main, "_make_bot", spy_make_bot)
        monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)

        result = CliRunner().invoke(main.cli, ["browser", "do it"])
        assert result.exit_code == 0, result.output
        assert captured["thinking_level"] == ""
