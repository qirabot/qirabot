"""LLM providers for the local decision engine (Vertex AI family).

All three providers authenticate with GCP ADC (service account JSON, gcloud
user credentials, or the GCE metadata server) via vertex_auth, and talk raw
REST over httpx — no provider SDKs.
"""

from .base import (
    ChatRequest,
    ChatResponse,
    ErrorCategory,
    Provider,
    ProviderError,
)
from .registry import ModelSpec, create_provider, parse_model, resolve_default_model

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "ErrorCategory",
    "ModelSpec",
    "Provider",
    "ProviderError",
    "create_provider",
    "parse_model",
    "resolve_default_model",
]
