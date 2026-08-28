"""LocalBackend typed-API semantics — the decision-behavior checklist from
plans/3.0-local-engine.md §3, exercised through the same typed calls the SDK
client makes (start_ai/AIRun.step, locate, extract, check_condition)."""

import io

import pytest

from qirabot.engine.local_backend import LocalBackend
from qirabot.engine.providers.base import ChatRequest, ChatResponse, ErrorCategory, ProviderError
from qirabot.engine.session import StepError
from qirabot.engine.types import TokenUsage, ToolCall

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


def start_run(b: LocalBackend, instruction: str = "buy a coffee", **kwargs):
    kwargs.setdefault("platform", "chrome")
    return b.start_ai(instruction, **kwargs)


def step(run, screenshot: bytes = PNG, action_result: str = ""):
    return run.step(screenshot, action_result, 1280, 720)


CLICK_ARGS = {"point_x": 500, "point_y": 500, "reason": "点击"}
DONE_ARGS = {"reason": "done", "success": True, "result": "买到了"}


class TestStartAI:
    def test_1_missing_instruction(self) -> None:
        b = backend(FakeProvider())
        with pytest.raises(ValueError, match="non-empty instruction"):
            b.start_ai("", platform="chrome")

    def test_1_empty_screenshot_rejected(self) -> None:
        run = start_run(backend(FakeProvider()))
        with pytest.raises(StepError, match="screenshot required"):
            step(run, b"")

    def test_invalid_custom_tools_rejected(self) -> None:
        b = backend(FakeProvider())
        with pytest.raises(ValueError, match="must match"):
            start_run(b, custom_tools=[{"name": "Bad Name"}])

    def test_invalid_exclude_tools_rejected(self) -> None:
        b = backend(FakeProvider())
        with pytest.raises(ValueError):
            start_run(b, exclude_tools=["no_such_tool"])

    def test_registration_logged(self, caplog) -> None:
        # The engine logs what a run registered — the SDK no longer echoes
        # it back per-response, so this is the visible registration record.
        import logging

        b = backend(FakeProvider())
        with caplog.at_level(logging.INFO, logger="qirabot.engine"):
            start_run(
                b,
                custom_tools=[{"name": "gm_command", "description": "GM 指令"}],
                exclude_tools=["scroll"],
                knowledge="GM 只能用一次",
            )
        messages = [r.getMessage() for r in caplog.records]
        assert any("custom tools registered" in m and "gm_command" in m for m in messages)
        assert any(
            f"knowledge registered: {len('GM 只能用一次'.encode())} bytes" in m
            for m in messages
        )

    def test_2_save_note_resend_keeps_history_alignment(self) -> None:
        # The SDK re-sends the previous frame after save_note (device state
        # didn't change); the engine keeps one screenshot per step so history
        # replay stays aligned.
        fake = FakeProvider(
            tool_resp("save_note", {"content": "第一页", "reason": "记录"}),
            tool_resp("done", DONE_ARGS),
        )
        run = start_run(backend(fake))
        assert step(run).action_type == "save_note"
        outcome = step(run, PNG, "ok")
        assert outcome.finished is True
        assert fake.requests[1].messages[-1].images[0].data == PNG


class TestRunIndependence:
    """Session lifetime belongs to the caller: every start_ai is a fresh
    session, and a run abandoned after a failure cannot leak into the next."""

    def test_run_after_step_error_starts_fresh(self) -> None:
        fake = FakeProvider(text_resp("garbage"), text_resp("garbage"))
        b = backend(fake)
        run = start_run(b)
        with pytest.raises(StepError):
            step(run)

        fake.push(tool_resp("done", DONE_ARGS))
        outcome = step(start_run(b, "换个新任务"))
        assert outcome.finished is True
        assert outcome.step_number == 1  # fresh session, not a continuation
        task_msgs = [
            m for m in fake.requests[-1].messages if m.content.startswith("Task: ")
        ]
        assert task_msgs[0].content == "Task: 换个新任务\n\nPlease begin."

    def test_run_after_provider_error_starts_fresh(self) -> None:
        fake = FakeProvider(
            ProviderError(
                "gemini-vertex", "quota", category=ErrorCategory.RATE_LIMITED, status_code=429
            )
        )
        b = backend(fake)
        run = start_run(b)
        with pytest.raises(ProviderError):
            step(run)

        fake.push(tool_resp("done", DONE_ARGS))
        assert step(start_run(b, "重试任务")).step_number == 1

    def test_abandoned_mid_run_session_cannot_hijack_next_run(self) -> None:
        # Abort residue: the caller drops a healthy mid-run handle. A new run
        # must re-read instruction/knowledge instead of inheriting the old.
        fake = FakeProvider(
            tool_resp("click", CLICK_ARGS),
            tool_resp("done", DONE_ARGS),
        )
        b = backend(fake)
        old = start_run(b, knowledge="k")
        step(old)  # then the caller abandons `old`

        outcome = step(start_run(b, "新任务", knowledge="k2"))
        assert outcome.step_number == 1
        # No replayed turns from the abandoned session.
        assert [m for m in fake.requests[-1].messages if m.role == "assistant"] == []


class TestActionResultBackfill:
    def test_3_backfilled_into_previous_turn(self) -> None:
        fake = FakeProvider(
            tool_resp("click", CLICK_ARGS),
            tool_resp("done", DONE_ARGS),
        )
        run = start_run(backend(fake))
        step(run)
        step(run, PNG, "ERROR: click missed")
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
        run = start_run(backend(fake))
        step(run)
        step(run)
        tool_msgs = [m for m in fake.requests[1].messages if m.role == "tool"]
        assert tool_msgs[0].tool_results[0].content == (
            "ok\nVerify the actual result from the screenshot"
        )


class TestGroundingLoop:
    def test_4_unparsable_then_success_shares_counter(self) -> None:
        fake = FakeProvider(text_resp("garbage"), tool_resp("click", CLICK_ARGS))
        outcome = step(start_run(backend(fake)))
        assert outcome.action_type == "click"
        # Second attempt got the English corrective hint appended last.
        hint = fake.requests[1].messages[-1].content
        assert hint.startswith("Your previous response was empty or not a valid tool call")
        # Deviation: BOTH attempts' tokens are reported (user pays the bill).
        assert outcome.token_usage.input_tokens == 20
        assert outcome.token_usage.output_tokens == 10

    def test_4_bad_coords_then_success(self) -> None:
        fake = FakeProvider(
            tool_resp("click", {"point_x": 1500, "point_y": 400, "reason": "r"}),
            tool_resp("click", CLICK_ARGS),
        )
        outcome = step(start_run(backend(fake)))
        assert outcome.action_type == "click"
        hint = fake.requests[1].messages[-1].content
        assert "point_x=1500" in hint and "Coordinates must be integers normalized" in hint
        assert outcome.token_usage.input_tokens == 20

    def test_4_combined_failures_get_one_retry_total(self) -> None:
        # unparsable then bad-coords: the shared counter is exhausted — no
        # third attempt (NOT 1+1 retries per failure kind).
        fake = FakeProvider(
            text_resp("garbage"),
            tool_resp("click", {"point_x": 1500, "point_y": 400, "reason": "r"}),
        )
        with pytest.raises(StepError) as exc_info:
            step(start_run(backend(fake)))
        assert "inline grounding: model returned no usable coordinates" in str(exc_info.value)
        assert len(fake.requests) == 2
        # Failed step still reports the real spend of both attempts.
        assert exc_info.value.usage.input_tokens == 20

    def test_4_unparsable_exhausted_still_reports_spend(self) -> None:
        # Both attempts unparsable: the step fails, but the error still
        # carries both attempts' tokens (user pays that bill too).
        fake = FakeProvider(text_resp("garbage"), text_resp("garbage"))
        with pytest.raises(StepError) as exc_info:
            step(start_run(backend(fake)))
        assert "parse decision response" in str(exc_info.value)
        assert exc_info.value.usage.input_tokens == 20
        assert exc_info.value.usage.output_tokens == 10

    def test_4_drag_error_message(self) -> None:
        bad_drag = {"start_point_x": 5000, "start_point_y": 1, "end_point_x": 1, "end_point_y": 1, "reason": "r"}
        fake = FakeProvider(tool_resp("drag", bad_drag), tool_resp("drag", bad_drag))
        with pytest.raises(StepError, match="drag returned no usable coordinates"):
            step(start_run(backend(fake)))


class TestCoordinateResolution:
    def test_5_point_rescaled_to_device_pixels(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        outcome = step(start_run(backend(fake)))
        # 500/1000 × (1280,720) — device dims, not the screenshot's.
        assert outcome.params["x"] == 640
        assert outcome.params["y"] == 360
        assert "point_x" not in outcome.params

    def test_5_pixel_salvage(self) -> None:
        fake = FakeProvider(
            tool_resp("click", {"point_x": 1100, "point_y": 1200, "reason": "r"})
        )
        run = start_run(backend(fake), platform="desktop")
        outcome = run.step(PNG, "", 1920, 1280)
        assert (outcome.params["x"], outcome.params["y"]) == (1100, 1200)

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
        p = step(start_run(backend(fake))).params
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
        outcome = step(start_run(backend(fake)))
        assert outcome.params == {"direction": "down", "amount": 300}
        assert "x" not in outcome.params

    def test_5_duration_ms_mapped_to_wire_duration(self) -> None:
        # The model emits the unit-suffixed schema name; the dispatched params
        # keep the legacy `duration` (also ms) key the SDK executors read.
        fake = FakeProvider(tool_resp("wait", {"duration_ms": 500, "reason": "r"}))
        outcome = step(start_run(backend(fake)))
        assert outcome.params["duration"] == 500
        assert "duration_ms" not in outcome.params

    def test_5_custom_tool_skips_grounding(self) -> None:
        fake = FakeProvider(
            tool_resp("gm_command", {"command": "add 100", "reason": "r"})
        )
        run = start_run(
            backend(fake),
            custom_tools=[{"name": "gm_command", "description": "GM 指令"}],
        )
        outcome = step(run)
        assert outcome.action_type == "gm_command"
        assert outcome.params == {"command": "add 100"}

    def test_6_history_replays_normalized_frame(self) -> None:
        fake = FakeProvider(
            tool_resp("click", CLICK_ARGS),
            tool_resp("done", DONE_ARGS),
        )
        run = start_run(backend(fake))
        step(run)
        step(run, PNG, "ok")
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
        run = start_run(backend(fake))
        outcome = step(run)
        assert outcome.output == "ok"
        # Note lands in the next decide's progress-context message (near the
        # tail of the contents — NOT the system prompt, which must stay
        # byte-stable for the provider cache prefix)...
        step(run, PNG, "ok")
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

    def test_7b_cumulative_resave_replaces_subsumed_notes(self) -> None:
        # A model re-saving the whole accumulated list each time must leave a
        # single copy in Saved notes, while the history triads keep replaying
        # the calls exactly as made (they are the provider cache prefix).
        fake = FakeProvider(
            tool_resp("save_note", {"content": '[{"t": "A"}]', "reason": "记录1"}),
            tool_resp("save_note", {"content": '[{"t": "A"}, {"t": "B"}]', "reason": "记录2"}),
            tool_resp("done", DONE_ARGS),
        )
        run = start_run(backend(fake))
        step(run)
        step(run, PNG, "ok")
        step(run, PNG, "ok")
        progress = [
            m.content
            for m in fake.requests[2].messages
            if m.content.startswith("# Progress context")
        ][0]
        assert '## Saved notes\n[{"t": "A"}, {"t": "B"}]' in progress
        assert progress.count('"A"') == 1  # first note replaced, not joined
        # History still replays both save_note calls untouched.
        replayed = [
            m.tool_calls[0].args["content"]
            for m in fake.requests[2].messages
            if m.role == "assistant" and m.tool_calls[0].name == "save_note"
        ]
        assert replayed == ['[{"t": "A"}]', '[{"t": "A"}, {"t": "B"}]']

    def test_8_done_output_and_success_flag(self) -> None:
        fake = FakeProvider(
            tool_resp("done", {"reason": "r", "success": False, "result": "需要登录"})
        )
        outcome = step(start_run(backend(fake)))
        assert outcome.finished is True
        assert outcome.output == "需要登录"
        # client.py reads params["success"] to distinguish goal_failed.
        assert outcome.params["success"] is False

    def test_step_number_advances_per_committed_step(self) -> None:
        fake = FakeProvider(
            tool_resp("click", CLICK_ARGS),
            tool_resp("done", DONE_ARGS),
        )
        run = start_run(backend(fake))
        assert step(run).step_number == 1
        assert step(run, PNG, "ok").step_number == 2


class TestSingleStepLocate:
    def test_10_success(self) -> None:
        fake = FakeProvider(
            tool_resp("report_location", {"found": True, "point_x": 500, "point_y": 500})
        )
        outcome = backend(fake).locate(real_png(1000, 1000), "登录按钮")
        assert outcome.found is True
        assert (outcome.x, outcome.y) == (500, 500)
        assert outcome.error == ""

    def test_10_retry_once_on_unparsable_accumulates_tokens(self) -> None:
        fake = FakeProvider(
            text_resp("no tool call"),
            tool_resp("report_location", {"found": True, "point_x": 500, "point_y": 500}),
        )
        outcome = backend(fake).locate(real_png(), "x")
        assert outcome.found is True
        assert len(fake.requests) == 2
        assert outcome.token_usage.input_tokens == 20  # both attempts billed

    def test_10_timeout_not_retried(self) -> None:
        fake = FakeProvider(
            ProviderError("gemini-vertex", "timed out", category=ErrorCategory.TIMEOUT)
        )
        outcome = backend(fake).locate(real_png(), "x")
        assert outcome.found is False
        assert len(fake.requests) == 1

    def test_10_unsupported_screenshot_zero_spend(self) -> None:
        fake = FakeProvider()
        outcome = backend(fake).locate(b"not an image", "x")
        assert outcome.found is False
        assert outcome.error == "screenshot format not supported for locate"
        assert fake.requests == []
        assert outcome.token_usage.input_tokens == 0

    def test_10_not_found(self) -> None:
        fake = FakeProvider(
            tool_resp("report_location", {"found": False, "error": "只看到注册"})
        )
        outcome = backend(fake).locate(real_png(), "登录")
        assert outcome.found is False
        assert outcome.error == "只看到注册"
        assert outcome.token_usage.input_tokens == 10  # spend is real, reported

    def test_10_missing_locate_param(self) -> None:
        with pytest.raises(ValueError, match="locate parameter required"):
            backend(FakeProvider()).locate(real_png(), "")


class TestConditionAndExtract:
    def test_12_condition_met_semantics(self) -> None:
        fake = FakeProvider(
            tool_resp("check_result", {"condition_met": True, "reason": "看到了"})
        )
        outcome = backend(fake).check_condition(PNG, "首页可见", platform="chrome")
        assert outcome.met is True
        assert outcome.reasoning == "看到了"

    def test_12_condition_unmet_is_not_a_failure(self) -> None:
        fake = FakeProvider(
            tool_resp("check_result", {"condition_met": False, "reason": "还在加载"})
        )
        outcome = backend(fake).check_condition(PNG, "加载完成", platform="chrome")
        assert outcome.met is False  # a valid verdict — wait_for polls on this
        assert outcome.reasoning == "还在加载"

    def test_12_missing_condition(self) -> None:
        with pytest.raises(ValueError, match="condition/assertion parameter required"):
            backend(FakeProvider()).check_condition(PNG, "")

    def test_13_extract(self) -> None:
        fake = FakeProvider(tool_resp("extract_result", {"result": "¥42.50"}))
        outcome = backend(fake).extract(PNG, "价格", platform="chrome")
        assert outcome.result == "¥42.50"
        assert outcome.token_usage.input_tokens == 10

    def test_13_extract_missing_instruction(self) -> None:
        with pytest.raises(ValueError, match="extract requires instruction parameter"):
            backend(FakeProvider()).extract(PNG, "")


class TestMisc:
    def test_provider_error_propagates_from_step(self) -> None:
        fake = FakeProvider(
            ProviderError("gemini-vertex", "quota", category=ErrorCategory.RATE_LIMITED, status_code=429)
        )
        with pytest.raises(ProviderError, match="quota"):
            step(start_run(backend(fake)))

    def test_thinking_level_passed_through(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        run = start_run(backend(fake), thinking_level="high")
        step(run)
        assert fake.requests[0].params["thinking_level"] == "high"

    def test_engine_default_params(self) -> None:
        # Backend defaults: without them the engine's fallback temperature
        # 0.2 (below Gemini 3's recommended 1.0) and the API-side media
        # resolution / thinking level apply, the latter defaulting to high
        # on Gemini 3.
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        step(start_run(backend(fake)))
        params = fake.requests[0].params
        assert params["temperature"] == 1.0
        assert params["media_resolution"] == "high"
        assert params["thinking_level"] == "low"

    def test_constructor_thinking_level_beats_default(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        b = LocalBackend(
            model="gemini-vertex/gemini-test", provider=fake, thinking_level="medium"
        )
        step(start_run(b))
        assert fake.requests[0].params["thinking_level"] == "medium"

    def test_media_resolution_override(self) -> None:
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        b = LocalBackend(
            model="gemini-vertex/gemini-test", provider=fake, media_resolution="medium"
        )
        step(start_run(b))
        assert fake.requests[0].params["media_resolution"] == "medium"

    def test_trace_writes_jsonl(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("QIRA_ENGINE_TRACE", str(tmp_path))
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        step(start_run(backend(fake)))
        trace = (tmp_path / "trace.jsonl").read_text(encoding="utf-8")
        assert '"action_type": "ai"' in trace
        assert '"click"' in trace
        assert '"instruction": "buy a coffee"' in trace
        # Screenshot stored by content hash, not inline.
        assert len(list(tmp_path.glob("*.img"))) == 1


class TestPlatformNormalization:
    def test_browser_maps_to_chrome(self) -> None:
        # SDK adapters report "browser"; the engine prompt/tool vocabulary is
        # "chrome" (same mapActPlatform normalization the server did). Without
        # it the chrome prompt/tools silently fall back to android.
        fake = FakeProvider(tool_resp("click", CLICK_ARGS))
        step(start_run(backend(fake), platform="browser"))
        assert fake.requests[0].system_prompt.startswith(
            "# Role\nYou are a UI automation agent for the Chrome browser platform"
        )
        assert any(t.name == "navigate" for t in fake.requests[0].tools)


class TestAuthSelection:
    """Constructor-time auth mode choice: Vertex API key vs ADC."""

    def test_api_key_mode_never_touches_adc(self, monkeypatch) -> None:
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
        monkeypatch.delenv("QIRA_GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        with pytest.raises(ValueError, match="QIRA_GEMINI_API_KEY"):
            LocalBackend(model="gemini/gemini-test")
