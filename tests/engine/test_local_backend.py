"""LocalBackend /act semantics — the 13-item fidelity checklist from
plans/3.0-local-engine.md §3, exercised through the same request/response
dicts the SDK client uses on the wire."""

import io
from typing import Any


from qirabot.engine.local_backend import LocalBackend
from qirabot.engine.providers.base import ChatRequest, ChatResponse, ErrorCategory, ProviderError
from qirabot.engine.session import history_config_for_provider
from qirabot.engine.types import ModelConfig, TokenUsage, ToolCall

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def real_png(width: int = 1000, height: int = 1000) -> bytes:
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (width, height)).save(buf, format="PNG")
    return buf.getvalue()


class FakeProvider:
    def __init__(self, *responses: ChatResponse | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[ChatRequest] = []

    def push(self, *responses: ChatResponse | Exception) -> None:
        self.responses.extend(responses)

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        self.requests.append(request)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_resp(name: str, args: dict, in_tok: int = 10, out_tok: int = 5) -> ChatResponse:
    return ChatResponse(
        tool_calls=[ToolCall(id=name, name=name, args=args)],
        token_usage=TokenUsage(input_tokens=in_tok, output_tokens=out_tok),
        model_used="fake-model",
    )


def text_resp(content: str) -> ChatResponse:
    return ChatResponse(
        content=content,
        token_usage=TokenUsage(input_tokens=10, output_tokens=5),
        model_used="fake-model",
    )


def backend(fake: FakeProvider, model: str = "gemini-vertex/gemini-test") -> LocalBackend:
    return LocalBackend(model=model, provider=fake)


def ai_request(
    instruction: str | None = "buy a coffee",
    action_result: str = "",
    extra_params: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if instruction is not None:
        params["instruction"] = instruction
        params["max_steps"] = kwargs.pop("max_steps", 20)
    params.update(extra_params or {})
    req: dict[str, Any] = {
        "action": {"type": "ai", "params": params},
        "device_info": {"platform": "chrome", "width": 1280, "height": 720},
        "step_seq": 1,
    }
    if action_result:
        req["action_result"] = action_result
    req.update(kwargs)
    return req


def single_request(action_type: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": {"type": action_type, "params": params},
        "device_info": {"platform": "chrome", "width": 1280, "height": 720},
        "step_seq": 1,
    }


CLICK_ARGS = {"point_x": 500, "point_y": 500, "reason": "点击"}
DONE_ARGS = {"reason": "done", "success": True, "result": "买到了"}


class TestBootstrap:
    def test_1_screenshot_checked_before_instruction(self) -> None:
        # Both missing: the screenshot error wins (server checks it first).
        b = backend(FakeProvider())
        resp = b.act(b"", ai_request(instruction=None))
        assert resp["success"] is False
        assert resp["error"] == "screenshot required for first request"

    def test_1_missing_instruction(self) -> None:
        b = backend(FakeProvider())
        resp = b.act(PNG, ai_request(instruction=None))
        assert resp["error"] == "ai action requires instruction parameter on first request"

    def test_2_empty_screenshot_reuses_cache(self) -> None:
        fake = FakeProvider(
            tool_resp("save_note", {"content": "第一页", "reason": "记录"}),
            tool_resp("done", DONE_ARGS),
        )
        b = backend(fake)
        assert b.act(PNG, ai_request())["success"] is True
        # save_note continuation: SDK skips the re-upload.
        resp = b.act(b"", ai_request(instruction=None, action_result="ok"))
        assert resp["success"] is True
        # The cached screenshot fed the decide call.
        assert fake.requests[1].messages[-1].images[0].data == PNG

    def test_2_no_cache_available(self) -> None:
        fake = FakeProvider(tool_resp("done", DONE_ARGS))
        b = backend(fake)
        b.act(PNG, ai_request())  # done -> session dropped, nothing cached
        fake.push(tool_resp("done", DONE_ARGS))
        resp = b.act(b"", ai_request())  # new first step without screenshot
        assert resp["error"] == "screenshot required for first request"

    def test_invalid_custom_tools_rejected(self) -> None:
        b = backend(FakeProvider())
        resp = b.act(
            PNG, ai_request(extra_params={"custom_tools": [{"name": "Bad Name"}]})
        )
        assert resp["success"] is False
        assert "must match" in resp["error"]


class TestActionResultBackfill:
    def test_3_backfilled_into_previous_turn(self) -> None:
        fake = FakeProvider(
            tool_resp("click", CLICK_ARGS),
            tool_resp("done", DONE_ARGS),
        )
        b = backend(fake)
        b.act(PNG, ai_request())
        b.act(PNG, ai_request(instruction=None, action_result="ERROR: click missed"))
        # The second decide's replayed tool result carries the SDK's report
        # plus the screenshot-verify nudge (item 11).
        tool_msgs = [m for m in fake.requests[1].messages if m.role == "tool"]
        assert tool_msgs[0].tool_results[0].content == (
            "ERROR: click missed\nVerify the actual result from the screenshot"
        )

    def test_11_default_ok_when_no_result_reported(self) -> None:
        fake = FakeProvider(
            tool_resp("click", CLICK_ARGS),
            tool_resp("done", DONE_ARGS),
        )
        b = backend(fake)
        b.act(PNG, ai_request())
        b.act(PNG, ai_request(instruction=None))
        tool_msgs = [m for m in fake.requests[1].messages if m.role == "tool"]
        assert tool_msgs[0].tool_results[0].content == (
            "ok\nVerify the actual result from the screenshot"
        )


class TestGroundingLoop:
    def test_4_unparsable_then_success_shares_counter(self) -> None:
        fake = FakeProvider(text_resp("garbage"), tool_resp("click", CLICK_ARGS))
        b = backend(fake)
        resp = b.act(PNG, ai_request())
        assert resp["success"] is True
        # Second attempt got the English corrective hint appended last.
        hint = fake.requests[1].messages[-1].content
        assert hint.startswith("Your previous response was empty or not a valid tool call")
        # Deviation: BOTH attempts' tokens are reported (user pays the bill).
        assert resp["inputTokens"] == 20
        assert resp["outputTokens"] == 10

    def test_4_bad_coords_then_success(self) -> None:
        fake = FakeProvider(
            tool_resp("click", {"point_x": 1500, "point_y": 400, "reason": "r"}),
            tool_resp("click", CLICK_ARGS),
        )
        b = backend(fake)
        resp = b.act(PNG, ai_request())
        assert resp["success"] is True
        hint = fake.requests[1].messages[-1].content
        assert "point_x=1500" in hint and "Coordinates must be integers normalized" in hint
        assert resp["inputTokens"] == 20

    def test_4_combined_failures_get_one_retry_total(self) -> None:
        # unparsable then bad-coords: the shared counter is exhausted — no
        # third attempt (NOT 1+1 retries per failure kind).
        fake = FakeProvider(
            text_resp("garbage"),
            tool_resp("click", {"point_x": 1500, "point_y": 400, "reason": "r"}),
        )
        b = backend(fake)
        resp = b.act(PNG, ai_request())
        assert resp["success"] is False
        assert resp["error"] == "inline grounding: model returned no usable coordinates"
        assert len(fake.requests) == 2
        # Failed step still reports the real spend of both attempts.
        assert resp["inputTokens"] == 20

    def test_4_unparsable_exhausted_still_reports_spend(self) -> None:
        # Both attempts unparsable: the step fails, but the error payload
        # still carries both attempts' tokens (user pays that bill too).
        fake = FakeProvider(text_resp("garbage"), text_resp("garbage"))
        b = backend(fake)
        resp = b.act(PNG, ai_request())
        assert resp["success"] is False
        assert "parse decision response" in resp["error"]
        assert resp["inputTokens"] == 20
        assert resp["outputTokens"] == 10

    def test_4_drag_error_message(self) -> None:
        bad_drag = {"start_point_x": 5000, "start_point_y": 1, "end_point_x": 1, "end_point_y": 1, "reason": "r"}
        fake = FakeProvider(tool_resp("drag", bad_drag), tool_resp("drag", bad_drag))
        b = backend(fake)
        resp = b.act(PNG, ai_request())
        assert resp["error"] == "inline grounding: drag returned no usable coordinates"


class TestCoordinateResolution:
    def test_5_point_rescaled_to_device_pixels(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        resp = backend(fake).act(PNG, ai_request())
        # 500/1000 × (1280,720) — device_info dims, not the screenshot's.
        assert resp["params"]["x"] == 640
        assert resp["params"]["y"] == 360
        assert "point_x" not in resp["params"]

    def test_5_pixel_salvage(self) -> None:
        fake = FakeProvider(
            tool_resp("click", {"point_x": 1100, "point_y": 1200, "reason": "r"})
        )
        req = ai_request()
        req["device_info"] = {"platform": "desktop", "width": 1920, "height": 1280}
        resp = backend(fake).act(PNG, req)
        assert (resp["params"]["x"], resp["params"]["y"]) == (1100, 1200)

    def test_5_drag_both_endpoints_and_anchor(self) -> None:
        fake = FakeProvider(
            tool_resp(
                "drag",
                {
                    "start_point_x": 100,
                    "start_point_y": 200,
                    "end_point_x": 900,
                    "end_point_y": 800,
                    "reason": "r",
                },
            )
        )
        resp = backend(fake).act(PNG, ai_request())
        p = resp["params"]
        assert (p["start_x"], p["start_y"]) == (128, 144)
        assert (p["end_x"], p["end_y"]) == (1152, 576)
        # Crosshair anchored at the start point.
        assert (p["x"], p["y"]) == (128, 144)
        for k in ("start_point_x", "start_point_y", "end_point_x", "end_point_y"):
            assert k not in p

    def test_5_non_targeting_action_passthrough(self) -> None:
        fake = FakeProvider(
            tool_resp("scroll", {"direction": "down", "amount": 300, "reason": "r"})
        )
        resp = backend(fake).act(PNG, ai_request())
        assert resp["params"] == {"direction": "down", "amount": 300}
        assert "x" not in resp["params"]

    def test_5_custom_tool_skips_grounding(self) -> None:
        fake = FakeProvider(
            tool_resp("gm_command", {"command": "add 100", "reason": "r"})
        )
        b = backend(fake)
        resp = b.act(
            PNG,
            ai_request(
                extra_params={
                    "custom_tools": [
                        {"name": "gm_command", "description": "GM 指令"}
                    ]
                }
            ),
        )
        assert resp["success"] is True
        assert resp["params"] == {"command": "add 100"}
        assert resp["tool_registration"] == {"registered": ["gm_command"], "excluded": []}

    def test_6_history_replays_normalized_frame(self) -> None:
        fake = FakeProvider(
            tool_resp("click", CLICK_ARGS),
            tool_resp("done", DONE_ARGS),
        )
        b = backend(fake)
        b.act(PNG, ai_request())
        b.act(PNG, ai_request(instruction=None, action_result="ok"))
        assistant = [m for m in fake.requests[1].messages if m.role == "assistant"][0]
        args = assistant.tool_calls[0].args
        # The model's own normalized coordinates plus the reason — never the
        # resolved device pixels.
        assert args == {"point_x": 500, "point_y": 500, "reason": "点击"}
        assert "x" not in args


class TestSaveNoteAndDone:
    def test_7_save_note_semantics(self) -> None:
        fake = FakeProvider(
            tool_resp("save_note", {"content": "第一页内容", "reason": "记录"}),
            tool_resp("done", DONE_ARGS),
        )
        b = backend(fake)
        resp = b.act(PNG, ai_request())
        assert resp["output"] == "ok"
        # Note lands in the next decide's progress-context message (near the
        # tail of the contents — NOT the system prompt, which must stay
        # byte-stable for the provider cache prefix)...
        b.act(PNG, ai_request(instruction=None, action_result="ok"))
        assert "## Saved notes" not in fake.requests[1].system_prompt
        progress = [
            m.content
            for m in fake.requests[1].messages
            if m.content.startswith("# Progress context")
        ]
        assert len(progress) == 1
        assert "## Saved notes\n第一页内容" in progress[0]
        # ...and the replayed turn's output is the pre-filled "ok".
        tool_msgs = [m for m in fake.requests[1].messages if m.role == "tool"]
        assert tool_msgs[0].tool_results[0].content.startswith("ok\n")

    def test_8_done_output_and_success_flag(self) -> None:
        fake = FakeProvider(
            tool_resp("done", {"reason": "r", "success": False, "result": "需要登录"})
        )
        b = backend(fake)
        resp = b.act(PNG, ai_request())
        assert resp["finished"] is True
        assert resp["success"] is True  # the step ran fine
        assert resp["output"] == "需要登录"
        # client.py reads params["success"] to distinguish goal_failed.
        assert resp["params"]["success"] is False
        # Session dropped: next ai request bootstraps fresh.
        fake.push(tool_resp("done", DONE_ARGS))
        resp2 = b.act(PNG, ai_request(instruction=None))
        assert resp2["error"] == "ai action requires instruction parameter on first request"

    def test_max_steps_drops_session_but_step_succeeds(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        b = backend(fake)
        resp = b.act(PNG, ai_request(max_steps=1))
        assert resp["success"] is True  # the step itself committed
        # Session gone: the next request is a fresh bootstrap.
        resp2 = b.act(PNG, ai_request(instruction=None))
        assert "instruction parameter" in resp2["error"]


class TestHistoryWindowByProvider:
    def test_9_all_providers_default(self) -> None:
        # No prefix-cache provider is left, so everything takes the default
        # window; the hook stays for Claude-style providers (see session.py).
        for provider in ("gemini-vertex", "gemini"):
            cfg = history_config_for_provider(
                ModelConfig(provider=provider, model="m"), max_steps=20
            )
            assert cfg.max_entries == 5
            assert cfg.max_screenshots == 1


class TestSingleStepLocate:
    def test_10_success(self) -> None:
        fake = FakeProvider(
            tool_resp("report_location", {"found": True, "point_x": 500, "point_y": 500})
        )
        resp = backend(fake).act(
            real_png(1000, 1000), single_request("click", {"locate": "登录按钮"})
        )
        assert resp["success"] is True
        assert resp["finished"] is True
        assert resp["actionType"] == "click"
        assert (resp["params"]["x"], resp["params"]["y"]) == (500, 500)
        assert resp["params"]["locate"] == "登录按钮"

    def test_10_retry_once_on_unparsable_accumulates_tokens(self) -> None:
        fake = FakeProvider(
            text_resp("no tool call"),
            tool_resp("report_location", {"found": True, "point_x": 500, "point_y": 500}),
        )
        resp = backend(fake).act(
            real_png(), single_request("click", {"locate": "x"})
        )
        assert resp["success"] is True
        assert len(fake.requests) == 2
        assert resp["inputTokens"] == 20  # both attempts billed

    def test_10_timeout_not_retried(self) -> None:
        fake = FakeProvider(
            ProviderError("gemini-vertex", "timed out", category=ErrorCategory.TIMEOUT)
        )
        resp = backend(fake).act(real_png(), single_request("click", {"locate": "x"}))
        assert resp["success"] is False
        assert len(fake.requests) == 1

    def test_10_unsupported_screenshot_zero_spend(self) -> None:
        fake = FakeProvider()
        resp = backend(fake).act(b"not an image", single_request("click", {"locate": "x"}))
        assert resp["error"] == "screenshot format not supported for locate"
        assert fake.requests == []
        assert "inputTokens" not in resp

    def test_10_not_found(self) -> None:
        fake = FakeProvider(
            tool_resp("report_location", {"found": False, "error": "只看到注册"})
        )
        resp = backend(fake).act(real_png(), single_request("click", {"locate": "登录"}))
        assert resp["success"] is False
        assert resp["error"] == "只看到注册"
        assert resp["inputTokens"] == 10  # spend is real, reported

    def test_10_missing_locate_param(self) -> None:
        resp = backend(FakeProvider()).act(real_png(), single_request("click", {}))
        assert resp["error"] == "locate parameter required"


class TestConditionAndExtract:
    def test_12_condition_met_semantics(self) -> None:
        fake = FakeProvider(
            tool_resp("check_result", {"condition_met": True, "reason": "看到了"})
        )
        resp = backend(fake).act(PNG, single_request("assert", {"condition": "首页可见"}))
        assert resp["success"] is True
        assert resp["finished"] is True
        assert resp["decision"] == "condition met"
        assert resp["output"] == "看到了"

    def test_12_condition_unmet_is_not_a_failure(self) -> None:
        fake = FakeProvider(
            tool_resp("check_result", {"condition_met": False, "reason": "还在加载"})
        )
        resp = backend(fake).act(PNG, single_request("wait_for", {"condition": "加载完成"}))
        assert resp["success"] is True  # the check ran — wait_for polls on this
        assert resp["finished"] is False
        assert resp["decision"] == "condition not met"

    def test_12_assertion_param_fallback(self) -> None:
        fake = FakeProvider(
            tool_resp("check_result", {"condition_met": True, "reason": "ok"})
        )
        resp = backend(fake).act(PNG, single_request("assert", {"assertion": "x"}))
        assert resp["success"] is True

    def test_12_missing_condition(self) -> None:
        resp = backend(FakeProvider()).act(PNG, single_request("assert", {}))
        assert resp["error"] == "condition/assertion parameter required"

    def test_13_extract(self) -> None:
        fake = FakeProvider(tool_resp("extract_result", {"result": "¥42.50"}))
        resp = backend(fake).act(PNG, single_request("extract", {"instruction": "价格"}))
        assert resp["success"] is True
        assert resp["finished"] is True
        assert resp["output"] == "¥42.50"

    def test_13_extract_missing_instruction(self) -> None:
        resp = backend(FakeProvider()).act(PNG, single_request("extract", {}))
        assert resp["error"] == "extract requires instruction parameter"


class TestRegistrationEcho:
    def test_first_step_echo(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        resp = backend(fake).act(
            PNG,
            ai_request(
                extra_params={
                    "custom_tools": [{"name": "gm_command", "description": "d"}],
                    "exclude_tools": ["scroll"],
                    "knowledge": "GM 只能用一次",
                }
            ),
        )
        assert resp["tool_registration"] == {
            "registered": ["gm_command"],
            "excluded": ["scroll"],
        }
        assert resp["knowledge_registered"] == len("GM 只能用一次".encode())
        assert "warning" not in resp

    def test_later_step_echo_warns(self) -> None:
        fake = FakeProvider(
            tool_resp("click", CLICK_ARGS),
            tool_resp("done", DONE_ARGS),
        )
        b = backend(fake)
        b.act(PNG, ai_request(extra_params={"knowledge": "k"}))
        resp = b.act(
            PNG,
            ai_request(instruction=None, action_result="ok", extra_params={"knowledge": "k2"}),
        )
        # Echo reports the session's effective value, not the incoming one.
        assert resp["knowledge_registered"] == 1
        assert "already registered" in resp["warning"]

    def test_absent_without_params(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        resp = backend(fake).act(PNG, ai_request())
        assert "tool_registration" not in resp
        assert "knowledge_registered" not in resp


class TestMisc:
    def test_unsupported_action_type(self) -> None:
        resp = backend(FakeProvider()).act(PNG, single_request("fly", {}))
        assert "unsupported action type" in resp["error"]

    def test_provider_error_becomes_failed_step(self) -> None:
        fake = FakeProvider(
            ProviderError("gemini-vertex", "quota", category=ErrorCategory.RATE_LIMITED, status_code=429)
        )
        resp = backend(fake).act(PNG, ai_request())
        assert resp["success"] is False
        assert "rate_limited" in resp["error"]

    def test_thinking_level_passed_through(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        req = ai_request()
        req["thinking_level"] = "high"
        backend(fake).act(PNG, req)
        assert fake.requests[0].params["thinking_level"] == "high"

    def test_engine_default_params(self) -> None:
        # v2-cloud-parity defaults: without them the provider zero-value
        # fallbacks apply (temperature 0.0 — below Gemini 3's recommended
        # 1.0 — and API-side media resolution / thinking level, the latter
        # defaulting to high on Gemini 3).
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        backend(fake).act(PNG, ai_request())
        params = fake.requests[0].params
        assert params["temperature"] == 1.0
        assert params["media_resolution"] == "high"
        assert params["thinking_level"] == "low"

    def test_constructor_thinking_level_beats_default(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        LocalBackend(
            model="gemini-vertex/gemini-test", provider=fake, thinking_level="medium"
        ).act(PNG, ai_request())
        assert fake.requests[0].params["thinking_level"] == "medium"

    def test_media_resolution_override(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        LocalBackend(
            model="gemini-vertex/gemini-test", provider=fake, media_resolution="medium"
        ).act(PNG, ai_request())
        assert fake.requests[0].params["media_resolution"] == "medium"

    def test_trace_writes_jsonl(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("QIRA_ENGINE_TRACE", str(tmp_path))
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        backend(fake).act(PNG, ai_request())
        trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
        assert '"action_type": "ai"' in trace
        assert '"actionType": "click"' in trace
        # Screenshot stored by content hash, not inline.
        assert len(list(tmp_path.glob("*.img"))) == 1


class TestPlatformNormalization:
    def test_browser_maps_to_chrome(self) -> None:
        # SDK adapters report "browser"; the engine prompt/tool vocabulary is
        # "chrome" (same mapActPlatform normalization the server did). Without
        # it the chrome prompt/tools silently fall back to android.
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        req = ai_request()
        req["device_info"] = {"platform": "browser", "width": 1280, "height": 720}
        backend(fake).act(PNG, req)
        assert fake.requests[0].cacheable_system_prompt.startswith(
            "# Role\nYou are a UI automation agent for the Chrome browser platform"
        )
        assert any(t.name == "navigate" for t in fake.requests[0].tools)


class TestAuthSelection:
    """Constructor-time auth mode choice: Vertex API key vs ADC."""

    def test_api_key_mode_never_touches_adc(self, monkeypatch) -> None:
        import pytest

        import qirabot.engine.local_backend as lb
        from qirabot.engine.providers.gemini_vertex import GeminiVertexProvider

        class ExplodingTokens:
            def __init__(self) -> None:
                pytest.fail("ADC must not be touched in API-key mode")

        monkeypatch.setattr(lb, "VertexTokenSource", ExplodingTokens)
        monkeypatch.delenv("QIRA_VERTEX_API_KEY", raising=False)
        b = LocalBackend(model="gemini-vertex/gemini-test", vertex_api_key="vk")
        try:
            assert isinstance(b._engine._provider, GeminiVertexProvider)
        finally:
            b.close()

    def test_gemini_provider_never_touches_adc(self, monkeypatch) -> None:
        import pytest

        import qirabot.engine.local_backend as lb
        from qirabot.engine.providers.gemini_api import GeminiApiProvider

        class ExplodingTokens:
            def __init__(self) -> None:
                pytest.fail("ADC must not be touched by the gemini provider")

        monkeypatch.setattr(lb, "VertexTokenSource", ExplodingTokens)
        monkeypatch.delenv("QIRA_GEMINI_API_KEY", raising=False)
        b = LocalBackend(model="gemini/gemini-test", gemini_api_key="sk")
        try:
            assert isinstance(b._engine._provider, GeminiApiProvider)
        finally:
            b.close()

    def test_gemini_provider_without_key_raises(self, monkeypatch) -> None:
        import pytest

        monkeypatch.delenv("QIRA_GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="QIRA_GEMINI_API_KEY"):
            LocalBackend(model="gemini/gemini-test")
