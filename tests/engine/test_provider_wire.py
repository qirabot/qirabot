"""Outbound wire-format tests for the Gemini providers, using
httpx.MockTransport — asserts the exact JSON go-llm produced where fidelity
matters (schema cleaning, functionResponse shape, thinking/media-resolution
mapping, API-key vs ADC auth)."""

import base64
import json
from typing import Any

import httpx
import pytest

from qirabot.engine.providers.base import ChatRequest, ErrorCategory, ProviderError
from qirabot.engine.providers.gemini_api import GeminiApiProvider
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

    def test_api_key_url_and_header(self) -> None:
        # API-key auth: global endpoint, short publishers/ path (the key is
        # project-bound server-side), x-goog-api-key instead of a Bearer token.
        client, seen = capture_client(GEMINI_OK)
        provider = GeminiVertexProvider("", "", None, client, api_key="vk-1")
        provider.chat(self.base_request(), timeout=30)
        req = seen[0]
        assert str(req.url) == (
            "https://aiplatform.googleapis.com/v1"
            "/publishers/google/models/gemini-2.5-flash:generateContent"
        )
        assert req.headers["x-goog-api-key"] == "vk-1"
        assert "Authorization" not in req.headers

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

    def two_image_messages(self) -> list[Message]:
        """History screenshot (tagged low) followed by the current one."""
        return [
            Message(role="user", images=[Image(mime_type="image/jpeg", data=b"old", resolution="low")]),
            Message(role="user", images=[Image(mime_type="image/jpeg", data=b"new")]),
        ]

    def image_parts(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        return [p for c in body["contents"] for p in c["parts"] if "inlineData" in p]

    def test_part_media_resolution_for_tagged_images(self) -> None:
        provider, seen = self.make()
        provider.chat(
            self.base_request(
                messages=self.two_image_messages(), params={"media_resolution": "medium"}
            ),
            timeout=30,
        )
        parts = self.image_parts(sent_body(seen[0]))
        assert parts[0]["mediaResolution"] == {"level": "MEDIA_RESOLUTION_LOW"}
        assert "mediaResolution" not in parts[1]  # untagged follows the global setting

    def test_part_media_resolution_omitted_when_equal_to_global(self) -> None:
        # A redundant per-part field would only risk a rejection on endpoints
        # without per-part support — never emit it.
        provider, seen = self.make()
        provider.chat(
            self.base_request(
                messages=self.two_image_messages(), params={"media_resolution": "low"}
            ),
            timeout=30,
        )
        assert all("mediaResolution" not in p for p in self.image_parts(sent_body(seen[0])))

    def test_part_media_resolution_emitted_when_global_unset(self) -> None:
        provider, seen = self.make()
        provider.chat(self.base_request(messages=self.two_image_messages()), timeout=30)
        parts = self.image_parts(sent_body(seen[0]))
        assert parts[0]["mediaResolution"] == {"level": "MEDIA_RESOLUTION_LOW"}

    def test_ultra_high_moves_to_image_parts(self) -> None:
        # ULTRA_HIGH is a per-part-only enum value: both endpoints reject it
        # in generationConfig with a 400. It must ride on the images instead,
        # without disturbing per-image overrides.
        provider, seen = self.make()
        provider.chat(
            self.base_request(
                messages=self.two_image_messages(),
                params={"media_resolution": "ultra_high"},
            ),
            timeout=30,
        )
        body = sent_body(seen[0])
        assert "mediaResolution" not in body["generationConfig"]
        parts = self.image_parts(body)
        assert parts[0]["mediaResolution"] == {"level": "MEDIA_RESOLUTION_LOW"}
        assert parts[1]["mediaResolution"] == {"level": "MEDIA_RESOLUTION_ULTRA_HIGH"}

    def test_ultra_high_downgrades_to_high_without_part_support(self) -> None:
        # When the endpoint rejects per-part fields there is nowhere legal
        # for ULTRA_HIGH to go: later requests fall back to the closest
        # request-level value instead of resending a guaranteed 400.
        rejection = {
            "error": {
                "code": 400,
                "message": (
                    "Invalid value at 'generate_content_request.contents[0]"
                    ".parts[0].media_resolution'"
                ),
            }
        }
        responses = iter([(400, rejection), (200, GEMINI_OK), (200, GEMINI_OK)])
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            status, payload = next(responses)
            return httpx.Response(status, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = GeminiVertexProvider("proj-1", "global", FakeTokens(), client)  # type: ignore[arg-type]

        req = self.base_request(
            messages=self.two_image_messages(), params={"media_resolution": "ultra_high"}
        )
        provider.chat(req, timeout=30)
        assert len(seen) == 2
        # The stripped retry carries no resolution anywhere.
        stripped = sent_body(seen[1])
        assert all("mediaResolution" not in p for p in self.image_parts(stripped))
        assert "mediaResolution" not in stripped["generationConfig"]

        # Sticky: the next chat sends the request-level fallback directly.
        provider.chat(req, timeout=30)
        body = sent_body(seen[2])
        assert body["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_HIGH"
        assert all("mediaResolution" not in p for p in self.image_parts(body))

    def test_part_media_resolution_fallback_and_sticky(self) -> None:
        # First call: endpoint rejects the part-level field (400 naming the
        # part path) -> the provider strips it, retries once, and never emits
        # it again on the same instance.
        rejection = {
            "error": {
                "code": 400,
                "message": (
                    "Invalid value at 'generate_content_request.contents[0]"
                    ".parts[0].media_resolution'"
                ),
            }
        }
        responses = iter([(400, rejection), (200, GEMINI_OK), (200, GEMINI_OK)])
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            status, payload = next(responses)
            return httpx.Response(status, json=payload)

        client = httpx.Client(transport=httpx.MockTransport(handler))
        provider = GeminiVertexProvider("proj-1", "global", FakeTokens(), client)  # type: ignore[arg-type]

        req = self.base_request(
            messages=self.two_image_messages(), params={"media_resolution": "medium"}
        )
        resp = provider.chat(req, timeout=30)
        assert resp.tool_calls[0].name == "click"
        assert len(seen) == 2
        assert any("mediaResolution" in p for p in self.image_parts(sent_body(seen[0])))
        stripped = sent_body(seen[1])
        assert all("mediaResolution" not in p for p in self.image_parts(stripped))
        # Everything else survives the strip, including the global setting.
        assert stripped["generationConfig"]["mediaResolution"] == "MEDIA_RESOLUTION_MEDIUM"

        # Sticky: the next chat builds without the field, no wasted request.
        provider.chat(req, timeout=30)
        assert len(seen) == 3
        assert all("mediaResolution" not in p for p in self.image_parts(sent_body(seen[2])))

    def test_unrelated_400_not_retried_without_part_fields(self) -> None:
        # A 400 that doesn't name the part field must fail fast (single
        # request) — the user pays for every attempt.
        client, seen = capture_client({"error": {"code": 400, "message": "bad schema"}}, status=400)
        provider = GeminiVertexProvider("proj-1", "global", FakeTokens(), client)  # type: ignore[arg-type]
        with pytest.raises(ProviderError) as ei:
            provider.chat(
                self.base_request(
                    messages=self.two_image_messages(), params={"media_resolution": "medium"}
                ),
                timeout=30,
            )
        assert ei.value.category == ErrorCategory.INVALID_REQUEST
        assert len(seen) == 1

    def test_part_rejection_without_part_fields_not_swallowed(self) -> None:
        # Same rejection text but the request never carried the field (e.g. a
        # server-side quirk): nothing to strip, so the error must propagate.
        rejection = {
            "error": {"code": 400, "message": "Invalid value at 'contents[0].parts[0].media_resolution'"}
        }
        client, seen = capture_client(rejection, status=400)
        provider = GeminiVertexProvider("proj-1", "global", FakeTokens(), client)  # type: ignore[arg-type]
        with pytest.raises(ProviderError):
            provider.chat(self.base_request(), timeout=30)
        assert len(seen) == 1

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



class TestGeminiApi:
    """Gemini Developer API (AI Studio keys): same body as gemini-vertex,
    different host/path/auth."""

    def base_request(self, **kwargs: Any) -> ChatRequest:
        defaults: dict[str, Any] = dict(
            model="gemini-2.5-flash",
            messages=[Message(role="user", content="Task: go")],
            tools=[ToolDefinition(name="click", description="c", parameters={"type": "object"})],
            force_tool=True,
            cacheable_system_prompt="CACHEABLE",
            system_prompt="DYNAMIC",
            params={"temperature": 0.2},
        )
        defaults.update(kwargs)
        return ChatRequest(**defaults)

    def test_url_auth_and_shared_body(self) -> None:
        client, seen = capture_client(GEMINI_OK)
        provider = GeminiApiProvider("sk-1", client)
        resp = provider.chat(self.base_request(), timeout=30)
        req = seen[0]
        assert str(req.url) == (
            "https://generativelanguage.googleapis.com/v1beta"
            "/models/gemini-2.5-flash:generateContent"
        )
        assert req.headers["x-goog-api-key"] == "sk-1"
        assert "Authorization" not in req.headers
        body = sent_body(req)
        assert body["systemInstruction"] == {"parts": [{"text": "CACHEABLEDYNAMIC"}]}
        assert body["toolConfig"] == {"functionCallingConfig": {"mode": "ANY"}}
        assert resp.tool_calls[0].name == "click"

    def test_http_error_classified(self) -> None:
        client, _ = capture_client({"error": "bad key"}, status=401)
        provider = GeminiApiProvider("sk-bad", client)
        with pytest.raises(ProviderError) as ei:
            provider.chat(self.base_request(), timeout=30)
        assert ei.value.category == ErrorCategory.AUTH
        assert ei.value.provider == "gemini"

    def test_part_media_resolution_fallback_wired(self) -> None:
        # The AI Studio provider shares run_chat with gemini-vertex; verify
        # the strip-and-retry path is wired here too.
        rejection = {
            "error": {"code": 400, "message": "Unknown name \"mediaResolution\" at 'contents[0].parts[0]'"}
        }
        responses = iter([(400, rejection), (200, GEMINI_OK)])
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            status, payload = next(responses)
            return httpx.Response(status, json=payload)

        provider = GeminiApiProvider("sk-1", httpx.Client(transport=httpx.MockTransport(handler)))
        provider.chat(
            self.base_request(
                messages=[
                    Message(role="user", images=[Image(mime_type="image/jpeg", data=b"old", resolution="low")]),
                ],
                params={"media_resolution": "medium"},
            ),
            timeout=30,
        )
        assert len(seen) == 2
        parts = [p for c in sent_body(seen[1])["contents"] for p in c["parts"] if "inlineData" in p]
        assert all("mediaResolution" not in p for p in parts)
