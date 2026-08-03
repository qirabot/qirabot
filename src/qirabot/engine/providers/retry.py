"""Transport-level retry with linear backoff.

Deliberately narrower than go-llm's retry-everything policy: only 429/5xx,
timeouts and connection errors are retried — deterministic failures
(400/401/403) fail fast because the user pays for every attempt. The attempt
budget stays at 3 (matching production server.toml retry_times) so
rate-limit-happy endpoints keep working. Content-level failures (empty or
unparsable responses) are NOT retried here; that is the engine's
corrective-re-decide path.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from .base import ProviderError

logger = logging.getLogger("qirabot.engine")

DEFAULT_ATTEMPTS = 3

T = TypeVar("T")


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, ProviderError):
        return exc.retryable
    # Transport hiccups: connect failures, resets, timeouts.
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


def with_retry(
    fn: Callable[[], T],
    attempts: int = DEFAULT_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Execute fn, retrying retryable failures with 1s→2s→3s (capped) backoff."""
    delay = 1.0
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc) or attempt >= attempts:
                raise
            last_exc = exc
            logger.warning(
                "retryable provider error (attempt %d/%d): %s", attempt, attempts, exc
            )
            sleep(delay)
            delay = min(delay + 1.0, 3.0)
    raise last_exc if last_exc else RuntimeError("unreachable")  # pragma: no cover
