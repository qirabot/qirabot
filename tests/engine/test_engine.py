"""LocalEngine entry points — ported from the Go engine_test.go corpus:
tool-call priority, JSON text fallback, meta-field filtering, and the locate
dialects with their differing salvage semantics."""

import io

import pytest

from qirabot.engine.engine import (
    LocalEngine,
    extract_json,
    filter_meta_fields,
    parse_response,
)
from qirabot.engine.providers.base import ChatRequest, ChatResponse
from qirabot.engine.types import (
    ConditionInput,
    DecisionInput,
    ExtractInput,
    LocateInput,
    LocateUnparsableError,
    ModelConfig,
    TokenUsage,
    ToolCall,
    UnparsableResponseError,
    UnsupportedScreenshotError,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def real_png(width: int = 100, height: int = 200) -> bytes:
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (width, height)).save(buf, format="PNG")
    return buf.getvalue()


class FakeProvider:
    def __init__(self, *responses: ChatResponse | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []
        self.timeouts: list[float] = []

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        self.requests.append(request)
        self.timeouts.append(timeout)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_resp(name: str, args: dict, **kwargs) -> ChatResponse:
    return ChatResponse(
        tool_calls=[ToolCall(id=name, name=name, args=args)],
        token_usage=TokenUsage(input_tokens=10, output_tokens=5),
        model_used="fake-model",
        **kwargs,
    )


def text_resp(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        token_usage=TokenUsage(input_tokens=10, output_tokens=5),
        model_used="fake-model",
        finish_reason="stop",
    )


def decide_input(**kwargs) -> DecisionInput:
    defaults = dict(
        instruction="do it", platform="chrome", current_screenshot=PNG, is_first_step=True
    )
    defaults.update(kwargs)
    return DecisionInput(**defaults)


class TestDecide:
    def test_tool_call_preferred_and_meta_filtered(self) -> None:
        fake = FakeProvider(
            tool_resp("click", {"point_x": 500, "point_y": 300, "reason": "点击登录"})
        )
        result = LocalEngine(fake).decide(decide_input())
        assert result.action is not None
        assert result.action.type == "click"
        # reason/safety_decision are meta fields, stripped from params.
        assert result.action.params == {"point_x": 500, "point_y": 300}
        assert result.action.reasoning == "点击登录"
        assert result.model_used == "fake-model"
        assert result.token_usage.input_tokens == 10

    def test_request_shape(self) -> None:
        fake = FakeProvider(tool_resp("done", {"reason": "r", "success": True, "result": "x"}))
        LocalEngine(fake, "default-model").decide(
            decide_input(model_config=ModelConfig(provider="gemini-vertex", model="m1"))
        )
        req = fake.requests[0]
        assert req.model == "m1"
        assert req.force_tool is True
        assert req.params["temperature"] == 0.2
        assert req.params["max_tokens"] == 4096
        assert req.system_prompt.startswith("# Role")
        assert "# Current task context" in req.system_prompt
        assert any(t.name == "done" for t in req.tools)
        assert fake.timeouts[0] == 120.0

    def test_model_config_params_override_defaults(self) -> None:
        fake = FakeProvider(tool_resp("done", {"reason": "r", "success": True, "result": "x"}))
        LocalEngine(fake).decide(
            decide_input(
                model_config=ModelConfig(model="m", params={"temperature": 0.9, "top_p": 0.5})
            )
        )
        assert fake.requests[0].params["temperature"] == 0.9
        assert fake.requests[0].params["top_p"] == 0.5
        assert fake.requests[0].params["max_tokens"] == 4096

    def test_json_fallback_with_fence(self) -> None:
        content = '```json\n{"name": "click", "input": {"point_x": 1}, "reason": "r"}\n```'
        fake = FakeProvider(text_resp(content))
        result = LocalEngine(fake).decide(decide_input())
        assert result.action is not None
        assert result.action.type == "click"
        assert result.action.params == {"point_x": 1}
        assert result.action.reasoning == "r"

    def test_json_fallback_bare_object(self) -> None:
        content = 'Sure, here: {"name": "wait", "input": {"duration": 500}} done'
        fake = FakeProvider(text_resp(content))
        result = LocalEngine(fake).decide(decide_input())
        assert result.action is not None
        assert result.action.type == "wait"

    def test_unparsable_raises_with_partial_result(self) -> None:
        fake = FakeProvider(text_resp("I cannot decide."))
        with pytest.raises(UnparsableResponseError) as ei:
            LocalEngine(fake).decide(decide_input())
        partial = ei.value.result  # type: ignore[attr-defined]
        # The token spend is real even though the response was unusable.
        assert partial.token_usage.input_tokens == 10
        assert partial.raw_response == "I cannot decide."

    def test_validate_input(self) -> None:
        fake = FakeProvider()
        with pytest.raises(ValueError, match="instruction is required"):
            LocalEngine(fake).decide(DecisionInput(current_screenshot=PNG))
        with pytest.raises(ValueError, match="screenshot is required"):
            LocalEngine(fake).decide(DecisionInput(instruction="x", is_first_step=False))
        # First step without screenshot is allowed.
        fake2 = FakeProvider(tool_resp("done", {"reason": "r", "success": True, "result": ""}))
        LocalEngine(fake2).decide(DecisionInput(instruction="x", is_first_step=True))


class TestExtractAndCondition:
    def test_extract(self) -> None:
        fake = FakeProvider(tool_resp("extract_result", {"result": "42.5"}))
        result = LocalEngine(fake).extract(
            ExtractInput(prompt="the price", screenshot=PNG, language="zh")
        )
        assert result.result == "42.5"
        req = fake.requests[0]
        assert req.tools[0].name == "extract_result"
        assert "Respond in 中文." in req.messages[0].content

    def test_extract_validation(self) -> None:
        engine = LocalEngine(FakeProvider())
        with pytest.raises(ValueError, match="prompt is required"):
            engine.extract(ExtractInput(screenshot=PNG))
        with pytest.raises(ValueError, match="screenshot is required"):
            engine.extract(ExtractInput(prompt="p"))

    def test_check_condition(self) -> None:
        fake = FakeProvider(
            tool_resp("check_result", {"condition_met": True, "reason": "看到了首页"})
        )
        result = LocalEngine(fake).check_condition(
            ConditionInput(condition="首页可见", screenshot=PNG)
        )
        assert result.met is True
        assert result.reasoning == "看到了首页"

    def test_check_condition_no_tool_call_defaults_unmet(self) -> None:
        fake = FakeProvider(text_resp("hmm"))
        result = LocalEngine(fake).check_condition(
            ConditionInput(condition="c", screenshot=PNG)
        )
        assert result.met is False
        assert result.reasoning == ""


class TestLocate:
    def test_point_dialect(self) -> None:
        fake = FakeProvider(
            tool_resp("report_location", {"found": True, "point_x": 500, "point_y": 500})
        )
        result = LocalEngine(fake).locate(
            LocateInput(locate="login button", screenshot=real_png(100, 200))
        )
        assert result.found
        assert (result.x, result.y) == (50, 100)
        assert fake.timeouts[0] == 60.0
        # Tool schema carries the point dialect fields.
        props = fake.requests[0].tools[0].parameters["properties"]
        assert "point_x" in props and "box_2d" not in props

    def test_point_dialect_pixel_salvage(self) -> None:
        fake = FakeProvider(
            tool_resp("report_location", {"found": True, "point_x": 1100, "point_y": 1500})
        )
        result = LocalEngine(fake).locate(
            LocateInput(locate="x", screenshot=real_png(1200, 1600))
        )
        assert (result.x, result.y) == (1100, 1500)

    def test_bbox_dialect(self) -> None:
        fake = FakeProvider(
            tool_resp("report_location", {"found": True, "box_2d": [100, 200, 300, 400]})
        )
        result = LocalEngine(fake).locate(
            LocateInput(
                locate="x",
                screenshot=real_png(1000, 1000),
                model_config=ModelConfig(locate_format="bbox_yx_1000"),
            )
        )
        assert result.found
        assert (result.x, result.y) == (300, 200)
        props = fake.requests[0].tools[0].parameters["properties"]
        assert "box_2d" in props and "point_x" not in props

    def test_bbox_no_salvage(self) -> None:
        # bbox declares 0-1000 in its schema; out-of-range is rejected, never
        # salvaged as pixels.
        fake = FakeProvider(
            tool_resp("report_location", {"found": True, "box_2d": [1100, 1200, 1300, 1400]})
        )
        with pytest.raises(LocateUnparsableError):
            LocalEngine(fake).locate(
                LocateInput(
                    locate="x",
                    screenshot=real_png(2000, 2000),
                    model_config=ModelConfig(locate_format="bbox_yx_1000"),
                )
            )

    def test_not_found(self) -> None:
        fake = FakeProvider(
            tool_resp("report_location", {"found": False, "error": "只看到注册按钮"})
        )
        result = LocalEngine(fake).locate(LocateInput(locate="登录", screenshot=real_png()))
        assert not result.found
        assert result.not_found_reason == "只看到注册按钮"

    def test_no_tool_call_raises_with_usage(self) -> None:
        fake = FakeProvider(text_resp("cannot"))
        with pytest.raises(LocateUnparsableError) as ei:
            LocalEngine(fake).locate(LocateInput(locate="x", screenshot=real_png()))
        # The spend is attached — the caller must bill/report it.
        assert ei.value.result.token_usage.input_tokens == 10

    def test_unsupported_screenshot_no_llm_call(self) -> None:
        fake = FakeProvider()
        with pytest.raises(UnsupportedScreenshotError):
            LocalEngine(fake).locate(LocateInput(locate="x", screenshot=b"not an image"))
        assert fake.requests == []  # zero spend

    def test_language_in_error_prop(self) -> None:
        fake = FakeProvider(tool_resp("report_location", {"found": True, "point_x": 1, "point_y": 1}))
        LocalEngine(fake).locate(
            LocateInput(locate="x", screenshot=real_png(), language="zh-CN")
        )
        error_desc = fake.requests[0].tools[0].parameters["properties"]["error"]["description"]
        assert error_desc.endswith("Respond in 中文")


class TestParsingHelpers:
    def test_extract_json(self) -> None:
        assert extract_json('```json\n{"a":1}\n```') == '{"a":1}'
        assert extract_json('noise {"a":{"b":2}} tail') == '{"a":{"b":2}}'
        assert extract_json("no json here") == ""
        assert extract_json("") == ""

    def test_parse_response_errors(self) -> None:
        with pytest.raises(ValueError, match="no JSON found"):
            parse_response("hello")
        with pytest.raises(ValueError, match="missing action name"):
            parse_response('{"input": {}}')

    def test_filter_meta_fields(self) -> None:
        out = filter_meta_fields({"a": 1, "reason": "r", "safety_decision": "s"})
        assert out == {"a": 1}
