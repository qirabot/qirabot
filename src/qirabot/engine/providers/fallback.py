"""Fallback model: when the primary model is out of capacity, re-send the
call to a second model on the same provider.

Same provider only, so the fallback shares the primary's auth, endpoint,
HTTP client and tier ladder — switching is a model-name swap on the request
and nothing else. The engine's conversation history is model-agnostic
(plain functionCall/functionResponse parts, no thought signatures), so a
run can switch mid-way and the fallback picks up where the primary left off.

Where it sits: outside the provider, so every free or cheaper remedy runs
first — transport retries (quota window, outage window), then tier
escalation — and switching models is the last move before a call fails.
The ai() step's own timeout re-ask sits outside this, and by then the
fallback is the active model.

Only capacity failures switch (timeout, rate limit, unavailable). The rest
are deterministic for the request or the account and would fail the same
way on any model.

Switching is sticky until :meth:`FallbackProvider.reset`, which the backend
calls at the start of each ai() run: within a run, re-probing a primary
that just burned a full budget would cost that budget again on every step;
across runs, the primary deserves a fresh chance.
"""

from __future__ import annotations

import dataclasses
import logging

from .base import ChatRequest, ChatResponse, ErrorCategory, Provider, ProviderError

logger = logging.getLogger("qirabot.engine")

_SWITCH_ON = frozenset(
    {ErrorCategory.TIMEOUT, ErrorCategory.RATE_LIMITED, ErrorCategory.UNAVAILABLE}
)


class FallbackProvider:
    """Wraps a provider; on a capacity failure, retries the call once with
    ``fallback_model`` and keeps using it until :meth:`reset`."""

    def __init__(self, inner: Provider, fallback_model: str, provider_name: str = "") -> None:
        self._inner = inner
        self._fallback = fallback_model
        self._name = provider_name or "provider"
        self._active = False
        self._calls = 0

    @property
    def fallback_model(self) -> str:
        return self._fallback

    @property
    def active(self) -> bool:
        """Whether calls are currently going to the fallback model."""
        return self._active

    @property
    def fallback_calls(self) -> int:
        """How many calls the fallback model answered, for the run report."""
        return self._calls

    def reset(self) -> None:
        """Send the next call to the primary model again."""
        self._active = False

    def chat(self, request: ChatRequest, timeout: float) -> ChatResponse:
        if self._active or request.model == self._fallback:
            return self._via_fallback(request, timeout)
        try:
            return self._inner.chat(request, timeout)
        except ProviderError as exc:
            if exc.category not in _SWITCH_ON:
                raise
            self._active = True
            logger.warning(
                "%s: model %s failed (%s); switching to fallback model %s "
                "until the next ai() run starts: %s",
                self._name,
                request.model,
                exc.category.value,
                self._fallback,
                exc,
            )
            return self._via_fallback(request, timeout)

    def _via_fallback(self, request: ChatRequest, timeout: float) -> ChatResponse:
        resp = self._inner.chat(dataclasses.replace(request, model=self._fallback), timeout)
        self._calls += 1
        return resp
