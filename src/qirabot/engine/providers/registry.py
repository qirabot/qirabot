"""Model string parsing, Vertex project/location resolution and provider
construction.

The user-facing model format is "{provider}/{model}" with provider one of
claude-vertex / gemini-vertex / gemini (the Gemini Developer API, AI Studio
keys). The model part may itself contain slashes, so the split is on the
first slash only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from .base import Provider
from .claude_vertex import ClaudeVertexProvider
from .gemini_api import GeminiApiProvider
from .gemini_vertex import GeminiVertexProvider
from .vertex_auth import VertexTokenSource

PROVIDER_CLAUDE_VERTEX = "claude-vertex"
PROVIDER_GEMINI_VERTEX = "gemini-vertex"
PROVIDER_GEMINI = "gemini"

SUPPORTED_PROVIDERS = (
    PROVIDER_CLAUDE_VERTEX,
    PROVIDER_GEMINI_VERTEX,
    PROVIDER_GEMINI,
)

# Default model per provider, used when the user names a provider without a
# model. Keep the gemini default in the Gemini 3 family: the thinkingLevel
# field the engine emits is Gemini-3-only (2.5 rejects it with a 400),
# verified against Vertex.
DEFAULT_MODELS: dict[str, str] = {
    PROVIDER_GEMINI_VERTEX: "gemini-3.6-flash",
    PROVIDER_CLAUDE_VERTEX: "claude-sonnet-5",
    PROVIDER_GEMINI: "gemini-3.6-flash",
}

# The model used when nothing is configured at all.
DEFAULT_MODEL = f"{PROVIDER_GEMINI_VERTEX}/{DEFAULT_MODELS[PROVIDER_GEMINI_VERTEX]}"


@dataclass
class ModelSpec:
    provider: str
    model: str


def parse_model(value: str) -> ModelSpec:
    """Parse "{provider}/{model}" (first-slash split). A bare provider name
    resolves to that provider's default model. Raises ValueError with a
    configuration hint otherwise."""
    value = value.strip()
    if not value:
        raise ValueError(_format_hint("model is required"))

    if "/" in value:
        provider, model = value.split("/", 1)
    else:
        provider, model = value, ""

    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(_format_hint(f'unknown provider "{provider}"'))

    if not model:
        model = DEFAULT_MODELS[provider]
    return ModelSpec(provider=provider, model=model)


def _format_hint(problem: str) -> str:
    return (
        f"{problem}; use model=\"{{provider}}/{{model}}\" with provider one of "
        f"{', '.join(SUPPORTED_PROVIDERS)} — e.g. model=\"{DEFAULT_MODEL}\""
    )


def resolve_default_model() -> str:
    """The model to use when the user did not specify one: QIRA_MODEL env or
    the built-in default."""
    return os.environ.get("QIRA_MODEL", "").strip() or DEFAULT_MODEL


def resolve_vertex_project(explicit: str, token_source: VertexTokenSource) -> str:
    """Project resolution: explicit param > QIRA_VERTEX_PROJECT >
    GOOGLE_CLOUD_PROJECT > the ADC credentials' own project id."""
    for candidate in (
        explicit,
        os.environ.get("QIRA_VERTEX_PROJECT", ""),
        os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
    ):
        if candidate.strip():
            return candidate.strip()
    project = token_source.adc_project()
    if project:
        return project
    raise ValueError(
        "no Google Cloud project configured; pass vertex_project=, set "
        "QIRA_VERTEX_PROJECT / GOOGLE_CLOUD_PROJECT, or use credentials that "
        "carry a project id"
    )


def resolve_vertex_api_key(explicit: str) -> str:
    """Vertex AI API key resolution: explicit param > QIRA_VERTEX_API_KEY.

    Deliberately does NOT read GOOGLE_API_KEY: that variable commonly holds
    an AI Studio key, which Vertex endpoints reject with an opaque 401 —
    an explicit qirabot-scoped variable keeps the failure mode out."""
    for candidate in (explicit, os.environ.get("QIRA_VERTEX_API_KEY", "")):
        if candidate.strip():
            return candidate.strip()
    return ""


def resolve_gemini_api_key(explicit: str) -> str:
    """Gemini Developer API (AI Studio) key resolution: explicit param >
    QIRA_GEMINI_API_KEY > GEMINI_API_KEY (the variable the official docs and
    SDKs use — unambiguous, unlike GOOGLE_API_KEY, which stays unread)."""
    for candidate in (
        explicit,
        os.environ.get("QIRA_GEMINI_API_KEY", ""),
        os.environ.get("GEMINI_API_KEY", ""),
    ):
        if candidate.strip():
            return candidate.strip()
    return ""


def resolve_vertex_location(explicit: str) -> str:
    """Location resolution: explicit param > QIRA_VERTEX_LOCATION >
    GOOGLE_CLOUD_LOCATION > "global" (same default as production)."""
    for candidate in (
        explicit,
        os.environ.get("QIRA_VERTEX_LOCATION", ""),
        os.environ.get("GOOGLE_CLOUD_LOCATION", ""),
    ):
        if candidate.strip():
            return candidate.strip()
    return "global"


def create_provider(
    spec: ModelSpec,
    project: str,
    location: str,
    token_source: VertexTokenSource | None,
    http_client: httpx.Client,
    api_key: str = "",
) -> Provider:
    if spec.provider == PROVIDER_CLAUDE_VERTEX:
        if api_key:
            raise ValueError(
                "Vertex AI API keys only cover Google's own models; "
                "claude-vertex requires ADC (gcloud auth application-default "
                "login or GOOGLE_APPLICATION_CREDENTIALS)"
            )
        if token_source is None:
            raise ValueError("claude-vertex requires a token source (ADC)")
        return ClaudeVertexProvider(project, location, token_source, http_client)
    if spec.provider == PROVIDER_GEMINI_VERTEX:
        return GeminiVertexProvider(
            project, location, token_source, http_client, api_key=api_key
        )
    if spec.provider == PROVIDER_GEMINI:
        if not api_key:
            raise ValueError(
                "the gemini provider (Gemini Developer API / AI Studio) "
                "requires an API key; pass gemini_api_key= or set "
                "QIRA_GEMINI_API_KEY / GEMINI_API_KEY"
            )
        return GeminiApiProvider(api_key, http_client)
    raise ValueError(_format_hint(f'unknown provider "{spec.provider}"'))
