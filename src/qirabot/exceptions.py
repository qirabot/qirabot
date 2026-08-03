"""Exceptions for Qirabot SDK."""

from __future__ import annotations

from typing import Any


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
    """Insufficient credit balance (402)."""


class RateLimitError(QirabotError):
    """Too many requests; the model provider is rate limiting (429).

    The engine's provider layer backs off and retries these internally.
    Exposed as its own type so callers can ``except RateLimitError`` to apply
    their own backoff or surface a "slow down" message.
    """


class ActionError(QirabotError):
    """AI action failed."""


class QirabotTimeoutError(QirabotError):
    """Operation timed out (client-side)."""


class QirabotConnectionError(QirabotError):
    """Could not reach the Qirabot server (DNS failure, refused connection, etc.).

    Distinct from :class:`QirabotTimeoutError`: the request never completed a
    round-trip because the host was unreachable, not because it was slow.
    """


class TaskTerminatedError(QirabotError):
    """The task was terminated server-side (console, orphan cleaner, or the
    max-duration cap) while the script was still running.

    Raised when ``/act`` answers with a ``control="terminated"`` response
    instead of executing the step. Not a script bug: the task's server record
    is already in a terminal state, so further steps would neither run nor be
    recorded. ``task_status`` carries that terminal state.
    """

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


_ERROR_CODE_MAP: dict[str, type[QirabotError]] = {
    "auth.token_missing": AuthenticationError,
    "auth.api_key_missing": AuthenticationError,
    "auth.api_key_invalid": AuthenticationError,
    "finance.insufficient_balance": InsufficientBalanceError,
}

_STATUS_CODE_MAP: dict[int, type[QirabotError]] = {
    401: AuthenticationError,
    402: InsufficientBalanceError,
    429: RateLimitError,
}


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


def raise_for_error(status_code: int, data: dict[str, Any]) -> None:
    """Raise the appropriate exception for an error response."""
    error = data.get("error", {})
    if isinstance(error, str):
        message = error or data.get("message", f"Request failed with status {status_code}")
        code = data.get("code")
    elif error:
        message = error.get("message", f"Request failed with status {status_code}")
        code = error.get("code")
    else:
        message = data.get("message", f"Request failed with status {status_code}")
        code = data.get("code")

    if code and code in _ERROR_CODE_MAP:
        raise _ERROR_CODE_MAP[code](message, code=code, status_code=status_code)

    exc_cls = _STATUS_CODE_MAP.get(status_code, QirabotError)
    raise exc_cls(message, code=code, status_code=status_code)
