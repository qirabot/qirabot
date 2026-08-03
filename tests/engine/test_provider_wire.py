"""Outbound wire-format tests for the Vertex providers, using
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

    @pytest.mark.parametrize(
        ("value", "want"),
        [
            ("low", "MEDIA_RESOLUTION_LOW"),
            ("medium", "MEDIA_RESOLUTION_MEDIUM"),
            ("HIGH", "MEDIA_RESOLUTION_HIGH"),
            ("ultra_high", "MEDIA_RESOLUTION_ULTRA_HIGH"),
            ("bogus", "MEDIA_RESOLUTION_MEDIUM"),  # go-llm's fallback
        ],
    )
    def test_media_resolution_mapping(self, value: str, want: str) -> None:
        provider, seen = self.make()
        provider.chat(
            self.base_request(params={"media_resolution": value}), timeout=30
        )
        assert sent_body(seen[0])["generationConfig"]["mediaResolution"] == want

    def test_no_media_resolution_when_unset(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(), timeout=30)
        assert "mediaResolution" not in sent_body(seen[0])["generationConfig"]

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

