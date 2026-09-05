"""Model string parsing and Vertex project/location/tier resolution.

The user-facing model format is "{provider}/{model}" with provider one of
gemini-vertex / gemini (the Gemini Developer API, AI Studio keys). The model
part may itself contain slashes, so the split is on the first slash only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import service_tier
from .vertex_auth import VertexTokenSource

PROVIDER_GEMINI_VERTEX = "gemini-vertex"
PROVIDER_GEMINI = "gemini"

SUPPORTED_PROVIDERS = (
    PROVIDER_GEMINI_VERTEX,
    PROVIDER_GEMINI,
)

# Default model per provider, used when the user names a provider without a
# model. Keep the gemini default in the Gemini 3 family: the thinkingLevel
# field the engine emits is Gemini-3-only (2.5 rejects it with a 400),
# verified against Vertex.
DEFAULT_MODELS: dict[str, str] = {
    PROVIDER_GEMINI_VERTEX: "gemini-3.8-flash",
    PROVIDER_GEMINI: "gemini-3.8-flash",
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


def resolve_service_tier(explicit: str) -> str:
    """Consumption tier resolution: explicit param > QIRA_SERVICE_TIER.
    Returns "" for the endpoint default (standard); raises ValueError on an
    unknown value rather than silently billing at the default tier."""
    for candidate in (explicit, os.environ.get("QIRA_SERVICE_TIER", "")):
        if candidate.strip():
            return service_tier.normalize(candidate)
    return ""


def resolve_tier_escalation(explicit: bool | None) -> bool:
    """Escalate-on-exhaustion resolution: explicit param >
    QIRA_TIER_ESCALATION. Off by default — moving up the ladder can raise the
    per-token rate, which is the user's call to make."""
    if explicit is not None:
        return explicit
    return os.environ.get("QIRA_TIER_ESCALATION", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
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


def check_tier_location(tier: str, location: str) -> None:
    """Vertex serves flex/priority on the global endpoint only, and a
    regional endpoint accepts the header and ignores it. Fail at construction
    instead of billing a whole run at standard rates while the user believes
    otherwise."""
    if tier and location and location != "global":
        raise ValueError(
            f'service_tier "{tier}" requires the global Vertex endpoint, but '
            f'location is "{location}"; unset vertex_location / '
            "QIRA_VERTEX_LOCATION / GOOGLE_CLOUD_LOCATION, or set it to "
            '"global"'
        )
