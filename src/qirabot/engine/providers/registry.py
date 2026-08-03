"""Model string parsing, Vertex project/location resolution and provider
construction.

The user-facing model format is "{provider}/{model}" with provider one of
claude-vertex / gemini-vertex. The model part may itself contain slashes,
so the split is on the first slash only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

from .base import Provider
from .claude_vertex import ClaudeVertexProvider
from .gemini_vertex import GeminiVertexProvider
from .vertex_auth import VertexTokenSource

PROVIDER_CLAUDE_VERTEX = "claude-vertex"
PROVIDER_GEMINI_VERTEX = "gemini-vertex"

SUPPORTED_PROVIDERS = (
    PROVIDER_CLAUDE_VERTEX,
    PROVIDER_GEMINI_VERTEX,
)

# Default model per provider, used when the user names a provider without a
# model. Keep the gemini default in the Gemini 3 family: the thinkingLevel
# field the engine emits is Gemini-3-only (2.5 rejects it with a 400),
# verified against Vertex.
DEFAULT_MODELS: dict[str, str] = {
    PROVIDER_GEMINI_VERTEX: "gemini-3.6-flash",
    PROVIDER_CLAUDE_VERTEX: "claude-sonnet-5",
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
    token_source: VertexTokenSource,
    http_client: httpx.Client,
) -> Provider:
    if spec.provider == PROVIDER_CLAUDE_VERTEX:
        return ClaudeVertexProvider(project, location, token_source, http_client)
    if spec.provider == PROVIDER_GEMINI_VERTEX:
        return GeminiVertexProvider(project, location, token_source, http_client)
    raise ValueError(_format_hint(f'unknown provider "{spec.provider}"'))
