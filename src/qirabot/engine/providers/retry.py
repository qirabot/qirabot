"""Transport-level retry with per-category backoff.

Deliberately narrower than go-llm's retry-everything policy: only 429/5xx,
timeouts and connection errors are retried — deterministic failures
(400/401/403) fail fast because the user pays for every attempt. The generic
attempt budget stays at 3 (matching production server.toml retry_times).
Content-level failures (empty or unparsable responses) are NOT retried here;
that is the engine's corrective-re-decide path.

Rate limits get their own longer schedule: Vertex/Gemini quotas are rolling
per-minute windows (RPM), so the generic 1s→2s schedule (~3s total) sits
entirely inside a closed window — a long ai() run would die on its first
quota brush, losing all accumulated progress. A rejected 429 request is not
billed, so waiting out the window costs nothing but wall-clock;
RATE_LIMIT_DELAYS spans a full minute cumulatively.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from .base import ErrorCategory, ProviderError

logger = logging.getLogger("qirabot.engine")

DEFAULT_ATTEMPTS = 3
RATE_LIMIT_DELAYS = (5.0, 10.0, 20.0, 30.0)

T = TypeVar("T")


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, ProviderError):
        return exc.retryable
    # Transport hiccups: connect failures, resets, timeouts.
    return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))


def _is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, ProviderError) and exc.category == ErrorCategory.RATE_LIMITED


def with_retry(
    fn: Callable[[], T],
    attempts: int = DEFAULT_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    """Execute fn, retrying retryable failures. Generic schedule: linear
    1s→2s→3s (capped) backoff over `attempts` tries; rate limits follow
    RATE_LIMIT_DELAYS instead (see module docstring)."""
    delay = 1.0
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            if _is_rate_limited(exc):
                if attempt > len(RATE_LIMIT_DELAYS):
                    raise
                wait = RATE_LIMIT_DELAYS[attempt - 1]
                budget = len(RATE_LIMIT_DELAYS) + 1
            else:
                if attempt >= attempts:
                    raise
                wait = delay
                delay = min(delay + 1.0, 3.0)
                budget = attempts
            logger.warning(
                "retryable provider error (attempt %d/%d, next try in %.0fs): %s",
                attempt,
                budget,
                wait,
                exc,
            )
            sleep(wait)
