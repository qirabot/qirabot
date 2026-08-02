"""Outbound wire-format tests for the three Vertex providers, using
httpx.MockTransport — asserts the exact JSON go-llm produced where fidelity
matters (cache_control placement, thinking/tool_choice exclusivity, schema
cleaning, functionResponse shape, dataURI images)."""

import base64
import json
from typing import Any

import httpx
import pytest

from qirabot.engine.providers.base import ChatRequest, ErrorCategory, ProviderError
from qirabot.engine.providers.claude_vertex import ClaudeVertexProvider
from qirabot.engine.providers.gemini_vertex import GeminiVertexProvider
from qirabot.engine.providers.vertex_openai import VertexOpenAIProvider
from qirabot.engine.types import Image, Message, TokenUsage, ToolCall, ToolDefinition, ToolResult


class FakeTokens:
    def __init__(self) -> None:
        self.calls = 0

    def token(self) -> str:
        self.calls += 1
        return f"tok-{self.calls}"

    def adc_project(self) -> str:
        return "adc-project"


def capture_client(response_json: dict[str, Any], status: int = 200) -> tuple[httpx.Client, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(status, json=response_json)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def sent_body(req: httpx.Request) -> dict[str, Any]:
    body = json.loads(req.content.decode("utf-8"))
    assert isinstance(body, dict)
    return body


CLAUDE_OK = {
    "content": [{"type": "tool_use", "id": "tu_1", "name": "click", "input": {"reason": "r"}}],
    "stop_reason": "tool_use",
    "usage": {
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_input_tokens": 80,
        "cache_creation_input_tokens": 5,
    },
}

GEMINI_OK = {
    "candidates": [
        {
            "content": {"parts": [{"functionCall": {"name": "click", "args": {"reason": "r"}}}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {
        "promptTokenCount": 100,
        "cachedContentTokenCount": 30,
        "candidatesTokenCount": 20,
        "thoughtsTokenCount": 7,
    },
}

OAI_OK = {
    "choices": [
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "click", "arguments": '{"reason":"r"}'},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    ],
    "usage": {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "completion_tokens_details": {"reasoning_tokens": 4},
        "prompt_tokens_details": {"cached_tokens": 60},
    },
}


class TestClaudeVertex:
    def make(self, response: dict[str, Any] = CLAUDE_OK, location: str = "global"):
        client, seen = capture_client(response)
        provider = ClaudeVertexProvider("proj-1", location, FakeTokens(), client)  # type: ignore[arg-type]
        return provider, seen

    def base_request(self, **kwargs: Any) -> ChatRequest:
        defaults: dict[str, Any] = dict(
            model="claude-sonnet-4-5@20250929",
            messages=[Message(role="user", content="Task: go")],
            tools=[ToolDefinition(name="click", description="c", parameters={"type": "object"})],
            force_tool=True,
            cacheable_system_prompt="CACHEABLE",
            system_prompt="DYNAMIC",
            params={"temperature": 0.2, "max_tokens": 4096},
        )
        defaults.update(kwargs)
        return ChatRequest(**defaults)

    def test_url_and_auth(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        req = seen[0]
        assert str(req.url) == (
            "https://aiplatform.googleapis.com/v1/projects/proj-1/locations/global"
            "/publishers/anthropic/models/claude-sonnet-4-5@20250929:rawPredict"
        )
        assert req.headers["Authorization"] == "Bearer tok-1"

    def test_regional_url(self) -> None:
        provider, seen = self.make(location="us-east5")
        provider.chat(self.base_request(), timeout=30)
        assert str(seen[0].url).startswith("https://us-east5-aiplatform.googleapis.com/v1/")

    def test_body_essentials(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        body = sent_body(seen[0])
        assert body["anthropic_version"] == "vertex-2023-10-16"
        assert "model" not in body  # model rides in the URL on Vertex
        assert body["max_tokens"] == 4096
        assert body["temperature"] == 0.2
        assert body["tool_choice"] == {"type": "any"}
        # System split: cacheable block gets the cache breakpoint, dynamic not.
        system = body["system"]
        assert system[0]["text"] == "CACHEABLE"
        assert system[0]["cache_control"] == {"type": "ephemeral"}
        assert system[1]["text"] == "DYNAMIC"
        assert "cache_control" not in system[1]

    def test_thinking_disables_forced_tool_choice_and_forces_temp(self) -> None:
        provider, seen = self.make()
        provider.chat(
            self.base_request(params={"temperature": 0.2, "thinking_level": "medium"}),
            timeout=30,
        )
        body = sent_body(seen[0])
        assert body["thinking"] == {"type": "adaptive"}
        assert body["output_config"] == {"effort": "medium"}
        assert body["temperature"] == 1
        assert body["tool_choice"] == {"type": "auto"}

    @pytest.mark.parametrize(
        ("level", "model", "want"),
        [
            ("minimal", "m", "low"),
            ("low", "m", "low"),
            ("medium", "m", "medium"),
            ("high", "m", "high"),
            ("xhigh", "claude-opus-4-7", "xhigh"),
            ("xhigh", "claude-sonnet-4-5", "high"),
            ("max", "m", "max"),
            ("weird", "m", "high"),
        ],
    )
    def test_effort_mapping(self, level: str, model: str, want: str) -> None:
        provider, seen = self.make()
        provider.chat(
            self.base_request(model=model, params={"thinking_level": level}), timeout=30
        )
        assert sent_body(seen[0])["output_config"]["effort"] == want

    def test_no_thinking_param_when_disabled(self) -> None:
        provider, seen = self.make()
        for level in ("", "disabled", "none"):
            provider.chat(self.base_request(params={"thinking_level": level}), timeout=30)
        for req in seen:
            assert "thinking" not in sent_body(req)

    def test_message_conversion_and_final_cache_breakpoint(self) -> None:
        provider, seen = self.make()
        messages = [
            Message(role="user", content="Task: go"),
            Message(role="user", images=[Image(mime_type="image/png", data=b"img1")]),
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="click", name="click", args={"point_x": 5})],
            ),
            Message(
                role="tool",
                tool_results=[ToolResult(tool_call_id="click", name="click", content="ok")],
            ),
            Message(role="user", images=[Image(mime_type="image/jpeg", data=b"img2")]),
        ]
        provider.chat(self.base_request(messages=messages), timeout=30)
        wire = sent_body(seen[0])["messages"]

        assert [m["role"] for m in wire] == ["user", "user", "assistant", "user", "user"]
        img_block = wire[1]["content"][0]
        assert img_block["type"] == "image"
        assert img_block["source"] == {
            "type": "base64",
            "media_type": "image/png",
            "data": base64.b64encode(b"img1").decode(),
        }
        tool_use = wire[2]["content"][0]
        assert tool_use == {
            "type": "tool_use",
            "id": "click",
            "name": "click",
            "input": {"point_x": 5},
        }
        tool_result = wire[3]["content"][0]
        assert tool_result["type"] == "tool_result"
        assert tool_result["tool_use_id"] == "click"
        # The final cache breakpoint rides the last non-assistant message's
        # last block — here the trailing screenshot.
        assert wire[4]["content"][-1]["cache_control"] == {"type": "ephemeral"}
        for m in wire[:4]:
            for block in m["content"]:
                assert "cache_control" not in block

    def test_parse_response(self) -> None:
        provider, _ = self.make()
        resp = provider.chat(self.base_request(), timeout=30)
        assert resp.finish_reason == "tool_calls"
        assert resp.tool_calls[0].name == "click"
        assert resp.tool_calls[0].args == {"reason": "r"}
        assert resp.token_usage == TokenUsage(
            input_tokens=100, output_tokens=20, cache_read_tokens=80, cache_write_tokens=5
        )

    def test_http_error_classified(self) -> None:
        client, _ = capture_client({"error": "denied"}, status=403)
        provider = ClaudeVertexProvider("p", "global", FakeTokens(), client)  # type: ignore[arg-type]
        with pytest.raises(ProviderError) as ei:
            provider.chat(self.base_request(), timeout=30)
        assert ei.value.category == ErrorCategory.AUTH
        assert ei.value.status_code == 403


class TestGeminiVertex:
    def make(self, response: dict[str, Any] = GEMINI_OK, location: str = "global"):
        client, seen = capture_client(response)
        provider = GeminiVertexProvider("proj-1", location, FakeTokens(), client)  # type: ignore[arg-type]
        return provider, seen

    def base_request(self, **kwargs: Any) -> ChatRequest:
        defaults: dict[str, Any] = dict(
            model="gemini-2.5-flash",
            messages=[Message(role="user", content="Task: go")],
            tools=[
                ToolDefinition(
                    name="click",
                    description="c",
                    parameters={
                        "type": "object",
                        "properties": {
                            "reason": {
                                "type": "string",
                                "description": "d",
                                "additionalProperties": False,  # must be dropped
                            },
                            "direction": {"type": "string", "enum": ["up", "down"]},
                        },
                        "required": ["reason"],
                        "additionalProperties": False,  # must be dropped
                    },
                )
            ],
            force_tool=True,
            cacheable_system_prompt="CACHEABLE",
            system_prompt="DYNAMIC",
            params={"temperature": 0.2, "max_tokens": 4096},
        )
        defaults.update(kwargs)
        return ChatRequest(**defaults)

    def test_url(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        assert str(seen[0].url) == (
            "https://aiplatform.googleapis.com/v1/projects/proj-1/locations/global"
            "/publishers/google/models/gemini-2.5-flash:generateContent"
        )

    def test_system_concatenated_and_mode_any(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        body = sent_body(seen[0])
        assert body["systemInstruction"] == {"parts": [{"text": "CACHEABLEDYNAMIC"}]}
        assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}
        assert len(body["safetySettings"]) == 4
        assert all(s["threshold"] == "OFF" for s in body["safetySettings"])

    def test_schema_cleaning(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        decl = sent_body(seen[0])["tools"][0]["functionDeclarations"][0]
        params = decl["parameters"]
        assert params["type"] == "OBJECT"
        assert "additionalProperties" not in params
        assert "additionalProperties" not in params["properties"]["reason"]
        assert params["properties"]["reason"]["type"] == "STRING"
        assert params["properties"]["direction"]["enum"] == ["up", "down"]
        assert params["required"] == ["reason"]

    def test_max_tokens_param_name_fidelity(self) -> None:
        # go-llm's Gemini path reads max_output_tokens, so the engine default
        # max_tokens=4096 never applied there — keep that quirk.
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        assert sent_body(seen[0])["generationConfig"]["maxOutputTokens"] == 8192
        provider.chat(self.base_request(params={"max_output_tokens": 2048}), timeout=30)
        assert sent_body(seen[1])["generationConfig"]["maxOutputTokens"] == 2048

    @pytest.mark.parametrize(
        ("level", "model", "want"),
        [
            ("disabled", "gemini-2.5-pro", "LOW"),
            ("disabled", "gemini-2.5-flash", "MINIMAL"),
            ("minimal", "gemini-2.5-pro", "LOW"),
            ("minimal", "gemini-2.5-flash", "MINIMAL"),
            ("low", "gemini-2.5-flash", "LOW"),
            ("medium", "gemini-2.5-flash", "MEDIUM"),
            ("high", "gemini-2.5-flash", "HIGH"),
            ("xhigh", "gemini-2.5-flash", "HIGH"),
        ],
    )
    def test_thinking_level_mapping(self, level: str, model: str, want: str) -> None:
        provider, seen = self.make()
        provider.chat(
            self.base_request(model=model, params={"thinking_level": level}), timeout=30
        )
        cfg = sent_body(seen[0])["generationConfig"]
        assert cfg["thinkingConfig"] == {"thinkingLevel": want}

    def test_no_thinking_config_when_unset(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        assert "thinkingConfig" not in sent_body(seen[0])["generationConfig"]

    def test_contents_conversion(self) -> None:
        provider, seen = self.make()
        messages = [
            Message(role="user", content="Task: go"),
            Message(role="user", images=[Image(mime_type="image/png", data=b"img")]),
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="click", name="click", args={"point_x": 5})],
            ),
            Message(
                role="tool",
                tool_results=[ToolResult(tool_call_id="click", name="click", content="ok")],
            ),
        ]
        provider.chat(self.base_request(messages=messages), timeout=30)
        contents = sent_body(seen[0])["contents"]
        assert [c["role"] for c in contents] == ["user", "user", "model", "user"]
        assert contents[1]["parts"][0]["inlineData"] == {
            "mimeType": "image/png",
            "data": base64.b64encode(b"img").decode(),
        }
        assert contents[2]["parts"][0]["functionCall"] == {
            "name": "click",
            "args": {"point_x": 5},
        }
        # functionResponse must directly follow its functionCall content.
        assert contents[3]["parts"][0]["functionResponse"] == {
            "name": "click",
            "response": {"output": "ok"},
        }

    def test_parse_response_usage_math(self) -> None:
        provider, _ = self.make()
        resp = provider.chat(self.base_request(), timeout=30)
        assert resp.tool_calls[0].name == "click"
        assert resp.tool_calls[0].id.startswith("call_")
        # input = prompt - cached; output = candidates + thoughts.
        assert resp.token_usage == TokenUsage(
            input_tokens=70, output_tokens=27, thinking_tokens=7, cache_read_tokens=30
        )

    def test_block_reason_maps_to_safety(self) -> None:
        blocked = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
        provider, _ = self.make(response=blocked)
        resp = provider.chat(self.base_request(), timeout=30)
        assert resp.finish_reason == "safety"
        assert resp.tool_calls == []


class TestVertexOpenAI:
    def make(self, response: dict[str, Any] = OAI_OK):
        client, seen = capture_client(response)
        provider = VertexOpenAIProvider("proj-1", "global", FakeTokens(), client)  # type: ignore[arg-type]
        return provider, seen

    def base_request(self, **kwargs: Any) -> ChatRequest:
        defaults: dict[str, Any] = dict(
            model="qwen/qwen3-vl-plus",
            messages=[Message(role="user", content="Task: go")],
            tools=[ToolDefinition(name="click", description="c", parameters={"type": "object"})],
            force_tool=True,
            cacheable_system_prompt="CACHEABLE",
            system_prompt="DYNAMIC",
            params={"temperature": 0.2, "max_tokens": 4096},
        )
        defaults.update(kwargs)
        return ChatRequest(**defaults)

    def test_url_and_model_verbatim(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        assert str(seen[0].url) == (
            "https://aiplatform.googleapis.com/v1/projects/proj-1/locations/global"
            "/endpoints/openapi/chat/completions"
        )
        body = sent_body(seen[0])
        # Publisher-prefixed model id passes through untouched.
        assert body["model"] == "qwen/qwen3-vl-plus"
        assert body["tool_choice"] == "required"
        assert body["max_tokens"] == 4096
        assert body["temperature"] == 0.2
        # System = cacheable + dynamic concatenated, as the first message.
        assert body["messages"][0] == {"role": "system", "content": "CACHEABLEDYNAMIC"}

    def test_zero_temperature_omitted(self) -> None:
        # go-llm only includes temperature/top_p when > 0.
        provider, seen = self.make()
        provider.chat(self.base_request(params={"temperature": 0}), timeout=30)
        assert "temperature" not in sent_body(seen[0])

    def test_images_ride_as_data_uris(self) -> None:
        provider, seen = self.make()
        messages = [
            Message(
                role="user",
                content="look",
                images=[Image(mime_type="image/png", data=b"img")],
            )
        ]
        provider.chat(self.base_request(messages=messages), timeout=30)
        parts = sent_body(seen[0])["messages"][1]["content"]
        assert parts[0] == {"type": "text", "text": "look"}
        uri = parts[1]["image_url"]["url"]
        assert uri == "data:image/png;base64," + base64.b64encode(b"img").decode()

    def test_tool_triad_wire(self) -> None:
        provider, seen = self.make()
        messages = [
            Message(
                role="assistant",
                tool_calls=[ToolCall(id="click", name="click", args={"point_x": 5})],
            ),
            Message(
                role="tool",
                tool_results=[ToolResult(tool_call_id="click", name="click", content="ok")],
            ),
        ]
        provider.chat(self.base_request(messages=messages), timeout=30)
        wire = sent_body(seen[0])["messages"]
        assistant = wire[1]
        assert assistant["tool_calls"][0]["id"] == "click"
        assert json.loads(assistant["tool_calls"][0]["function"]["arguments"]) == {"point_x": 5}
        tool_msg = wire[2]
        assert tool_msg == {"role": "tool", "tool_call_id": "click", "content": "ok"}

    def test_enable_thinking_flag(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(params={"thinking_level": "high"}), timeout=30)
        assert sent_body(seen[0])["chat_template_kwargs"] == {"enable_thinking": True}
        provider.chat(self.base_request(params={"thinking_level": "disabled"}), timeout=30)
        assert "chat_template_kwargs" not in sent_body(seen[1])

    def test_parse_response(self) -> None:
        provider, _ = self.make()
        resp = provider.chat(self.base_request(), timeout=30)
        assert resp.tool_calls[0].args == {"reason": "r"}
        assert resp.token_usage == TokenUsage(
            input_tokens=100, output_tokens=20, thinking_tokens=4, cache_read_tokens=60
        )

    def test_malformed_arguments_kept_raw(self) -> None:
        bad = {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {"name": "click", "arguments": "{oops"},
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ]
        }
        provider, _ = self.make(response=bad)
        resp = provider.chat(self.base_request(), timeout=30)
        assert resp.tool_calls[0].args == {"raw": "{oops"}

    def test_empty_choices(self) -> None:
        provider, _ = self.make(response={"choices": []})
        resp = provider.chat(self.base_request(), timeout=30)
        assert resp.finish_reason == "stop"
        assert resp.tool_calls == []
