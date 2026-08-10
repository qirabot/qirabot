"""GCP ADC token source for the Vertex providers.

Same semantics as go-llm's google.DefaultTokenSource: Application Default
Credentials resolve, in order, GOOGLE_APPLICATION_CREDENTIALS (service
account JSON), gcloud user credentials, and the GCE metadata server. Tokens
are cached on the credentials object and refreshed just before expiry.
"""

from __future__ import annotations

import threading

from .base import ErrorCategory, ProviderError

_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def vertex_base_url(location: str) -> str:
    """Vertex API base for a location; "global" uses the plain host."""
    if location == "global":
        return "https://aiplatform.googleapis.com/v1"
    return f"https://{location}-aiplatform.googleapis.com/v1"


class VertexTokenSource:
    """Lazily resolves ADC and hands out fresh Bearer tokens.

    Thread-safe: the SDK's ai() loop is single-threaded, but heartbeat-style
    callers must not race a refresh.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._credentials: object | None = None
        self._adc_project: str = ""

    def _load(self) -> None:
        try:
            import google.auth
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise ProviderError(
                "vertex",
                "google-auth is required for Vertex providers "
                "(pip install google-auth)",
                category=ErrorCategory.AUTH,
            ) from exc
        try:
            credentials, project = google.auth.default(scopes=[_CLOUD_PLATFORM_SCOPE])
        except Exception as exc:
            raise ProviderError(
                "vertex",
                "no Google Cloud credentials found; set "
                "GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON or run "
                "`gcloud auth application-default login` — or, for gemini-vertex "
                "models, use a Vertex AI API key instead (vertex_api_key= / "
                f"QIRA_VERTEX_API_KEY) ({exc})",
                category=ErrorCategory.AUTH,
            ) from exc
        self._credentials = credentials
        self._adc_project = project or ""

    def token(self) -> str:
        """Return a valid access token, refreshing if needed."""
        with self._lock:
            if self._credentials is None:
                self._load()
            credentials = self._credentials
            assert credentials is not None
            valid = bool(getattr(credentials, "valid", False))
            if not valid:
                try:
                    # Inside the try: the transport needs the `requests`
                    # package, and a missing-dependency ImportError must
                    # surface with the same auth context as a refresh failure.
                    import google.auth.transport.requests

                    credentials.refresh(  # type: ignore[attr-defined]
                        google.auth.transport.requests.Request()
                    )
                except Exception as exc:
                    raise ProviderError(
                        "vertex",
                        f"failed to refresh Google Cloud credentials: {exc}",
                        category=ErrorCategory.AUTH,
                    ) from exc
            token = getattr(credentials, "token", None)
            if not isinstance(token, str) or not token:
                raise ProviderError(
                    "vertex",
                    "Google Cloud credentials produced no access token",
                    category=ErrorCategory.AUTH,
                )
            return token

    def adc_project(self) -> str:
        """The project id carried by the ADC credentials, if any (used as the
        last fallback when no project is configured explicitly)."""
        with self._lock:
            if self._credentials is None:
                self._load()
            return self._adc_project
