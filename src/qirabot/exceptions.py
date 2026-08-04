"""Exceptions for Qirabot SDK.

Some classes are deprecated and never raised anymore (marked in their
docstrings). They stay exported because removing a public exception breaks
every ``except`` written against it; removal is scheduled for the next
major release.
"""

from __future__ import annotations


class QirabotError(Exception):
    """Base exception for all Qirabot SDK errors."""

    def __init__(
        self,
        message: str,
        code: str | None = None,
        status_code: int | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        super().__init__(message)

    def __str__(self) -> str:
        if self.code:
            return f"[{self.code}] {self.message}"
        return self.message


class AuthenticationError(QirabotError):
    """API key is missing or invalid (401)."""


class InsufficientBalanceError(QirabotError):
    """Deprecated, never raised: billing goes through your own model
    provider, which reports quota problems its own way. Kept exported for
    existing ``except`` clauses."""


class RateLimitError(QirabotError):
    """Deprecated, never raised: the engine's provider layer retries
    model-provider rate limits internally and surfaces persistent ones as
    :class:`ActionError`. Kept exported for existing ``except`` clauses."""


class ActionError(QirabotError):
    """AI action failed."""


class QirabotTimeoutError(QirabotError):
    """Operation timed out (client-side)."""


class QirabotConnectionError(QirabotError):
    """Deprecated, never raised: model-provider connectivity failures
    surface as :class:`AuthenticationError` (at construction) or
    :class:`ActionError` (mid-run). Kept exported for existing ``except``
    clauses."""


class TaskTerminatedError(QirabotError):
    """Deprecated, never raised: tasks run locally, so nothing can
    terminate them from outside the script. Kept exported (with its
    ``task_status`` field) for existing ``except`` clauses."""

    def __init__(
        self,
        message: str,
        task_status: str = "",
        code: str | None = None,
        status_code: int | None = None,
    ):
        super().__init__(message, code=code, status_code=status_code)
        self.task_status = task_status


class MissingDependencyError(QirabotError, ImportError):
    """An optional backend dependency (e.g. playwright, pyautogui) is not installed.

    Raised by :func:`qirabot._optional.require` with an actionable ``python -m pip
    install "qirabot[<extra>]"`` hint instead of a bare ``ModuleNotFoundError``
    traceback.
    """


# TaskTerminatedError is a control-plane verdict, not a transient failure —
# every retry would hit the same gate.
_NON_RETRYABLE = (AuthenticationError, InsufficientBalanceError, TaskTerminatedError)


def _is_retryable(error: QirabotError) -> bool:
    """Return True if the error is worth retrying."""
    if isinstance(error, _NON_RETRYABLE):
        return False
    if error.status_code and error.status_code < 500 and error.status_code not in (408, 429):
        return False
    return True
