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

Every delay is jittered: both schedules are fixed, so concurrent qirabot
processes that brush the same quota (a CI matrix, a multi-device run) would
otherwise retry in lockstep and keep colliding.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from typing import TypeVar

import httpx

from .base import ErrorCategory, ProviderError

logger = logging.getLogger("qirabot.engine")

DEFAULT_ATTEMPTS = 3
RATE_LIMIT_DELAYS = (5.0, 10.0, 20.0, 30.0)
JITTER_FRACTION = 0.2

T = TypeVar("T")


def jitter(delay: float) -> float:
    """Spread a scheduled delay by ±JITTER_FRACTION to desynchronize callers."""
    return delay * (1.0 + random.uniform(-JITTER_FRACTION, JITTER_FRACTION))


def is_retryable(exc: Exception) -> bool:
    if isinstance(exc, ProviderError):
        return exc.retryable
    # A read timeout means the model was reached and is just slow — see
    # RETRYABLE_CATEGORIES. Every other transport failure (connect, reset,
    # pool) never delivered the request, so a retry is cheap.
    if isinstance(exc, httpx.ReadTimeout):
        return False
    return isinstance(exc, httpx.TransportError)


def _is_rate_limited(exc: Exception) -> bool:
    return isinstance(exc, ProviderError) and exc.category == ErrorCategory.RATE_LIMITED


def with_retry(
    fn: Callable[[], T],
    attempts: int = DEFAULT_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[float], float] = jitter,
    wait_out_quota: bool = True,
) -> T:
    """Execute fn, retrying retryable failures. Generic schedule: linear
    1s→2s→3s (capped) backoff over `attempts` tries; rate limits follow
    RATE_LIMIT_DELAYS instead (see module docstring) and are **not** capped
    by `attempts` — waiting out a quota window is free, so callers who
    shorten the generic budget still get the full window. Each delay passes
    through `jitter` — tests inject the identity to assert the schedule.

    `wait_out_quota=False` puts rate limits on the generic schedule too, for
    callers that have already spent a quota window elsewhere and would only
    be stalling the run by spending a second one."""
    delay = 1.0
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception as exc:
            if not is_retryable(exc):
                raise
            if _is_rate_limited(exc) and wait_out_quota:
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
            wait = jitter(wait)
            logger.warning(
                "retryable provider error (attempt %d/%d, next try in %.0fs): %s",
                attempt,
                budget,
                wait,
                exc,
            )
            sleep(wait)
