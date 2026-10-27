"""Consumption tier selection (standard / flex / priority) for the two
Gemini transports.

One user-facing value, two wire encodings: Vertex takes an HTTP request
header, the Gemini Developer API takes a top-level ``service_tier`` body
field. Both are advisory — the endpoint may serve a different tier than the
one asked for and only says so after the fact, so every response is checked
against the request.

Billing follows what was *served*, not what was requested: an over-capacity
Priority request is downgraded to Standard and charged at Standard rates.
That asymmetry is what makes escalation safe to offer — its downside is a
wasted round trip, not a doubled bill.

Both tiers are global-endpoint only on Vertex; the caller is responsible for
rejecting a regional location, because the endpoint accepts the header and
silently ignores it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TypeVar

from .base import ErrorCategory, ProviderError

logger = logging.getLogger("qirabot.engine")

STANDARD = "standard"
FLEX = "flex"
PRIORITY = "priority"
SUPPORTED_TIERS = (STANDARD, FLEX, PRIORITY)

# Cheapest first. escalate() walks exactly one rung up on a capacity failure:
# flex is sheddable, standard queues behind priority.
_LADDER = (FLEX, STANDARD, PRIORITY)

# Vertex: request header, and the served tier echoed in usageMetadata.
VERTEX_TIER_HEADER = "X-Vertex-AI-LLM-Shared-Request-Type"
_VERTEX_TRAFFIC_TYPES = {
    STANDARD: "ON_DEMAND",
    FLEX: "ON_DEMAND_FLEX",
    PRIORITY: "ON_DEMAND_PRIORITY",
}
_TIERS_BY_TRAFFIC_TYPE = {v: k for k, v in _VERTEX_TRAFFIC_TYPES.items()}

# Gemini Developer API: served tier comes back as a response header.
GEMINI_SERVED_TIER_HEADER = "x-gemini-service-tier"

# Flex trades latency for price and the engine's per-call budgets assume
# standard-tier timing. The delay varies with model and endpoint load, but
# its shape does not: one Vertex sample (gemini-3.5-flash, 2026-08) put it in
# the low tens of seconds per request and flat across response sizes, i.e.
# queueing rather than slower generation. So a budget needs absolute
# headroom, not a proportional bump — 1.5x leaves 60s spare on a decide and
# 30s on a locate. Deliberately not the documented worst-case SLO, which is
# in minutes and would turn a stalled step into a hang. Applies only with
# escalation off; see FLEX_PROBE_TIMEOUT for the other case.
FLEX_TIMEOUT_SCALE = 1.5

# With escalation on, a flex attempt is a probe we are happy to abandon, so
# it gets a short leash instead of the widened one: standard is right there,
# and every second spent waiting on a queue is a second the run is stalled.
# Sized off the observed served-flex latency (low tens of seconds) with room
# to spare — a flex call that blows this is queued, not merely slow. Callers
# who would rather wait than pay standard rates turn escalation off, which
# restores the widened budget.
FLEX_PROBE_TIMEOUT = 30.0

T = TypeVar("T")


def normalize(value: str) -> str:
    """Validate a user-supplied tier, returning "" for the default.

    "" and "standard" both mean "send nothing": standard is what the
    endpoints do without a tier selector, so an explicit standard request is
    just the untouched wire format.
    """
    tier = value.strip().lower()
    if not tier or tier == STANDARD:
        return ""
    if tier not in SUPPORTED_TIERS:
        raise ValueError(
            f'unknown service_tier "{value}"; expected one of '
            f"{', '.join(SUPPORTED_TIERS)}"
        )
    return tier


def wire_tier(tier: str) -> str:
    """The value a transport should encode for a ladder position. Standard is
    expressed by sending nothing — neither endpoint takes it as a literal."""
    return "" if tier == STANDARD else tier


def vertex_traffic_type(tier: str) -> str:
    """The usageMetadata.trafficType value a Vertex request at `tier` should
    come back with."""
    return _VERTEX_TRAFFIC_TYPES.get(tier or STANDARD, "")


def tier_from_traffic_type(traffic_type: str) -> str:
    """Inverse of :func:`vertex_traffic_type`; "" for unrecognized values."""
    return _TIERS_BY_TRAFFIC_TYPE.get(traffic_type.strip().upper(), "")


_CAPACITY_FAILURES = (ErrorCategory.RATE_LIMITED, ErrorCategory.UNAVAILABLE)


def escalate(tier: str, exc: ProviderError) -> str:
    """The next rung up the ladder, or "" when no other tier could help.

    Only capacity failures qualify — everything else is deterministic and
    would fail the same way on any tier. Timeouts count on every tier. On
    flex that is its characteristic failure: a queue that never reaches the
    request, which expires the client budget instead of being refused. On
    standard it is how an overloaded endpoint usually shows itself — the
    request sits in a queue behind priority traffic until the budget runs
    out — and jumping that queue is exactly what priority sells. A timeout
    that really was a slow generation costs one more attempt, billed at the
    higher rate only if priority is what serves it.
    """
    capacity = _CAPACITY_FAILURES + (ErrorCategory.TIMEOUT,)
    if exc.category not in capacity:
        return ""
    index = _LADDER.index(tier or STANDARD)
    if index + 1 >= len(_LADDER):
        return ""
    return _LADDER[index + 1]


class TierLadder:
    """Per-provider tier state: where requests go, and whether escalation has
    parked them somewhere other than the configured tier.

    Escalation is sticky. Without that it repeats per call, and the probe is
    exactly the expensive failure it exists to avoid — a congested tier would
    charge every step of an `ai()` run the full cost of discovering it is
    still congested. One probe per provider decides it; a fresh bot probes
    again.
    """

    def __init__(self, tier: str, escalation: bool, provider: str) -> None:
        self._escalation = escalation
        self._provider = provider
        self._tier = tier
        self._parked = False

    @property
    def parked(self) -> bool:
        return self._parked

    def _may_escalate(self) -> bool:
        return self._escalation and not self._parked

    def wait_out_quota(self, escalated: bool) -> bool:
        """Whether a 429 on this call should sit through the full quota
        window.

        Yes for a first attempt on any tier, flex included: the window is a
        rolling minute, waiting it out is free, and escalating costs money —
        on flex it doubles the rate, which is the opposite of why you chose
        it. No once escalated, because the tier below already spent a window
        and the tiers commonly share one bucket.

        The failure that would be expensive to repeat on flex — a probe
        that burns its whole timeout — is already non-retryable at the
        transport (see RETRYABLE_CATEGORIES); a 503 comes back at once, so
        retrying it a couple of times to stay on half-price capacity is
        nearly free. How long a 503 is retried is :meth:`ride_out_outage`.
        """
        return not escalated

    def ride_out_outage(self, tier: str, escalated: bool) -> bool:
        """Whether a 503 / unreachable endpoint on this call should sit
        through the long outage schedule rather than the generic ~3s one.

        Yes by default: a refused request is not billed, and a brown-out
        that outlasts three quick tries would otherwise end the run. Two
        exceptions, both where a better move exists than waiting:

        - an escalated call — the tier below already rode one out, and the
          tiers commonly fail together;
        - a flex probe with escalation on — flex sheds load when its queue
          is deep, which will not have drained in half a minute, and
          standard is one rung away.
        """
        if escalated:
            return False
        return not (tier == FLEX and self._may_escalate())

    def timeout_for(self, tier: str, budget: float) -> float:
        """The client budget for one attempt at `tier`.

        A flex attempt we are willing to abandon gets a short leash; one we
        have to live with gets the widened budget instead."""
        if tier != FLEX:
            return budget
        if self._may_escalate():
            return min(budget, FLEX_PROBE_TIMEOUT)
        return budget * FLEX_TIMEOUT_SCALE

    def run(self, call: Callable[[str, bool], T]) -> T:
        """Run ``call(tier, escalated)``, retrying once one rung up when the
        tier is out of capacity, then staying there.

        Placed *outside* the transport's own backoff on purpose: waiting out
        a rolling per-minute quota window is free, escalating is not, so the
        free remedy is exhausted first and this is the last move before
        giving up and losing an in-progress run.
        """
        tier = self._tier
        try:
            return call(tier, False)
        except ProviderError as exc:
            target = escalate(tier, exc) if self._may_escalate() else ""
            if not target:
                raise
            # Park before the retry, not after it: what we learned is that
            # the tier below is congested, and that is true whether or not
            # this particular call goes on to succeed.
            self._tier = wire_tier(target)
            self._parked = True
            logger.warning(
                "%s: %s tier is out of capacity (%s); moving to %s for the "
                "rest of this session — billed at the %s rate only when %s "
                "is actually served",
                self._provider,
                tier or STANDARD,
                exc.category.value,
                target,
                target,
                target,
            )
            return call(self._tier, True)


def tier_label(configured: str, served: tuple[str, ...]) -> str:
    """Run-report text naming the tier that was billed.

    Never empty: the tier sets the price, so the report always names it. A
    run that asked for nothing and got standard reads ``standard — default``.
    When the tier asked for is not the one that ran, the served side is
    spelled out rather than abbreviated — it is the side that set the rate,
    and a reader should not have to decode it. That includes an unconfigured
    run that escalation moved to priority: it asked for standard, and the
    bill says otherwise.
    """
    asked = configured or STANDARD
    others = [t for t in dict.fromkeys(served) if t != asked]
    if not others:
        return configured or f"{STANDARD} — default"
    names = ", ".join(others)
    if asked in served:
        return f"{asked} — some calls served as {names}, and billed at that rate"
    return f"{asked} — served as {names}, and billed at that rate"


class TierCheck:
    """Watches what the endpoint actually served.

    Two jobs: warn once when it is not what was asked for, and remember the
    tiers seen so the run report can name what was billed.

    The endpoints report no reason for a downgrade — a downgraded request is
    an ordinary 200 whose only tell is the served-tier field — so the warning
    names the config it can see instead, letting the reader rule out the two
    documented preconditions and land on the third cause: entitlement.

    The warning is sticky by design: whatever caused one downgrade downgrades
    every request, and the engine makes one call per step.
    """

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._warned = False
        self._served: dict[str, None] = {}

    @property
    def served(self) -> tuple[str, ...]:
        """Distinct served tiers, in the order first seen."""
        return tuple(self._served)

    def observe(self, requested: str, served: str, model: str, where: str) -> None:
        if served:
            self._served[served] = None
        if self._warned or not requested or not served or served == requested:
            return
        self._warned = True
        logger.warning(
            "%s: requested the %s tier but the request was served as %s — "
            "billed at %s rates, and the endpoint gives no reason. "
            "Config seen: model=%s %s. If the model supports %s on this "
            "endpoint, what is left is entitlement or capacity: Vertex "
            "%s PayGo has organization-level ramp limits, and the Gemini "
            "Developer API gates %s behind its higher paid tiers. Further "
            "downgrades this session are not logged.",
            self._provider,
            requested,
            served,
            served,
            model or "unknown",
            where,
            requested,
            requested,
            requested,
        )
