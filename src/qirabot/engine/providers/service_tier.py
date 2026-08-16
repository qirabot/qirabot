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
# in minutes and would turn a stalled step into a hang.
FLEX_TIMEOUT_SCALE = 1.5

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


def escalate(tier: str, exc: ProviderError) -> str:
    """The next rung up the ladder, or "" when no other tier could help.

    Only rate limits and capacity errors qualify — everything else is
    deterministic and would fail the same way on any tier.
    """
    if exc.category not in (ErrorCategory.RATE_LIMITED, ErrorCategory.UNAVAILABLE):
        return ""
    index = _LADDER.index(tier or STANDARD)
    if index + 1 >= len(_LADDER):
        return ""
    return _LADDER[index + 1]


def with_escalation(
    tier: str,
    enabled: bool,
    provider: str,
    call: Callable[[str], T],
) -> T:
    """Run ``call(tier)``, retrying once one rung up when the tier is out of
    capacity.

    Placed *outside* the transport's own backoff on purpose: waiting out a
    rolling per-minute quota window is free, escalating is not, so the free
    remedy is exhausted first and this is the last move before giving up and
    losing an in-progress run.
    """
    try:
        return call(tier)
    except ProviderError as exc:
        target = escalate(tier, exc) if enabled else ""
        if not target:
            raise
        logger.warning(
            "%s: %s tier is out of capacity (%s); retrying once on %s — "
            "billed at the %s rate only if %s is actually served",
            provider,
            tier or STANDARD,
            exc.category.value,
            target,
            target,
            target,
        )
        return call(wire_tier(target))


class TierCheck:
    """One-shot warning when the endpoint serves a tier other than the one
    requested.

    Sticky by design: a non-global Vertex location, a model without tier
    support, or an account lacking Priority entitlement all downgrade every
    single request, and the engine makes one call per step.
    """

    def __init__(self, provider: str) -> None:
        self._provider = provider
        self._warned = False

    def observe(self, requested: str, served: str) -> None:
        if self._warned or not requested or not served or served == requested:
            return
        self._warned = True
        logger.warning(
            "%s: requested the %s tier but the request was served as %s — "
            "billed at %s rates. Tier selection needs the global endpoint and "
            "a model that supports it; capacity limits also downgrade "
            "silently. Further downgrades this session are not logged.",
            self._provider,
            requested,
            served,
            served,
        )
