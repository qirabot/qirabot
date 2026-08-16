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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class RetryBudget:
    attempts: int
    wait_out_quota: bool


def retry_budget(
    tier: str, escalation: bool, escalated: bool, default: int
) -> RetryBudget:
    """How much to spend inside `tier` before giving up on it.

    An escalated call skips the rate-limit schedule: the tier below already
    waited out a full quota window, and the tiers commonly share one bucket,
    so a second minute of sleeping stalls the run for capacity that is not
    coming. It still gets the generic budget for transport blips.

    Flex with somewhere to go gets exactly one attempt: a queue deep enough
    to shed or to blow the widened budget will not have drained a second
    later, and each retry costs a full flex-sized timeout. Handing off beats
    fighting. Without escalation the normal budget applies — there is nowhere
    else to go, so the retries are all the caller has.
    """
    if escalated:
        return RetryBudget(default, wait_out_quota=False)
    if tier == FLEX and escalation:
        return RetryBudget(1, wait_out_quota=True)
    return RetryBudget(default, wait_out_quota=True)


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
    would fail the same way on any tier. For flex that set includes
    timeouts: its characteristic failure is a queue that never reaches the
    request, which expires the client budget instead of being refused, and
    a queue is exactly the thing another tier fixes. On the tiers that are
    served promptly a timeout means something else — a slow generation —
    so it stays non-escalating there.
    """
    capacity = _CAPACITY_FAILURES + ((ErrorCategory.TIMEOUT,) if tier == FLEX else ())
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

    def budget(self, escalated: bool, default: int) -> RetryBudget:
        return retry_budget(self._tier, self._may_escalate(), escalated, default)

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


class TierCheck:
    """One-shot warning when the endpoint serves a tier other than the one
    requested.

    The endpoints report no reason for a downgrade — a downgraded request is
    an ordinary 200 whose only tell is the served-tier field — so the warning
    names the config it can see instead, letting the reader rule out the two
    documented preconditions and land on the third cause: entitlement.

    Sticky by design: whatever caused one downgrade downgrades every request,
    and the engine makes one call per step.
    """

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._warned = False

    def observe(self, requested: str, served: str, model: str, where: str) -> None:
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
