"""Per-provider ledger of where model-call wall-clock went.

Every request a transport sends is timed around its HTTP round trip and lands
in exactly one bucket:

- ``model_ms`` — answered (HTTP 200 with a usable body): the model's own
  latency, measured rather than derived;
- ``rate_limit_wait_ms`` — refused with a 429;
- ``outage_wait_ms`` — a 5xx, or the endpoint was never reached (connect,
  pool, reset);
- ``timeout_wait_ms`` — no answer inside the budget (read timeout, 408/504).

The transport's backoff sleeps between attempts go to the bucket of the
failure that caused them, so for one ``chat()`` call the four buckets add up
to its wall-clock minus local overhead (token refresh, body build, parse).
Deterministic failures (400/401/403, an unusable body) end the call at once
and are not a capacity problem, so they land nowhere.

A ledger rather than a return value on purpose: the calls worth seeing are
the failed ones, and their exceptions cross half a dozen layers (retry, tier
ladder, fallback, engine, session) on the way out. Readers take
:meth:`ProviderStats.snapshot` and subtract two snapshots to get a window,
e.g. one ai() step.

No lock: a provider is driven from one thread at a time, like the client's
own usage totals — and with concurrent callers a snapshot diff would not
mean anything anyway.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import httpx

from .base import ErrorCategory, ProviderError

# Backoff categories, as with_retry reports them to its on_wait hook.
RATE_LIMIT = "rate_limit"
OUTAGE = "outage"


@dataclass(frozen=True)
class StatsSnapshot:
    """The ledger's totals at one point in time.

    Field names match the public ``SessionUsage`` / ``StepResult`` fields,
    so a snapshot spreads straight into either. Subtracting two snapshots
    gives the activity between them."""

    model_ms: int = 0
    rate_limit_wait_ms: int = 0
    outage_wait_ms: int = 0
    timeout_wait_ms: int = 0

    def __sub__(self, other: StatsSnapshot) -> StatsSnapshot:
        return StatsSnapshot(
            **{
                f.name: getattr(self, f.name) - getattr(other, f.name)
                for f in dataclasses.fields(self)
            }
        )


class ProviderStats:
    """The running ledger one transport writes to (see the module docstring).

    Times accumulate as float seconds and are rounded to whole milliseconds
    only when read, so many short requests do not each lose a fraction."""

    def __init__(self) -> None:
        self._model = 0.0
        self._rate_limit = 0.0
        self._outage = 0.0
        self._timeout = 0.0

    def record_ok(self, seconds: float) -> None:
        """An answered request."""
        self._model += seconds

    def record_failure(self, exc: BaseException, seconds: float) -> None:
        """A failed request, booked under why it failed. Failures that are
        not about capacity are ignored."""
        if isinstance(exc, ProviderError):
            if exc.category == ErrorCategory.RATE_LIMITED:
                self._rate_limit += seconds
            elif exc.category == ErrorCategory.UNAVAILABLE:
                self._outage += seconds
            elif exc.category == ErrorCategory.TIMEOUT:
                self._timeout += seconds
        elif isinstance(exc, httpx.ReadTimeout):
            # The transports classify this one themselves (TIMEOUT); kept
            # here so a raw one is booked the same way.
            self._timeout += seconds
        elif isinstance(exc, httpx.TransportError):
            # Connect, reset, protocol errors: the failures with_retry rides
            # out on its outage schedule.
            self._outage += seconds

    def record_wait(self, category: str, seconds: float) -> None:
        """A backoff sleep before the next attempt (with_retry's on_wait)."""
        if category == RATE_LIMIT:
            self._rate_limit += seconds
        elif category == OUTAGE:
            self._outage += seconds

    def snapshot(self) -> StatsSnapshot:
        return StatsSnapshot(
            model_ms=_ms(self._model),
            rate_limit_wait_ms=_ms(self._rate_limit),
            outage_wait_ms=_ms(self._outage),
            timeout_wait_ms=_ms(self._timeout),
        )


def _ms(seconds: float) -> int:
    return int(round(seconds * 1000))
