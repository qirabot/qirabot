"""Tests for exception classes."""

import pytest

from qirabot.exceptions import (
    ActionError,
    AuthenticationError,
    MissingDependencyError,
    QirabotError,
    QirabotTimeoutError,
)


class TestQirabotError:
    def test_str_with_code(self):
        e = QirabotError("something broke", code="test.error")
        assert str(e) == "[test.error] something broke"

    def test_str_without_code(self):
        e = QirabotError("something broke")
        assert str(e) == "something broke"

    def test_attributes(self):
        e = QirabotError("msg", code="c", status_code=500)
        assert e.message == "msg"
        assert e.code == "c"
        assert e.status_code == 500


class TestExceptionHierarchy:
    @pytest.mark.parametrize("cls", [
        AuthenticationError,
        ActionError,
        QirabotTimeoutError,
        MissingDependencyError,
    ])
    def test_subclass_of_qirabot_error(self, cls):
        assert issubclass(cls, QirabotError)

    @pytest.mark.parametrize("cls", [
        AuthenticationError,
        ActionError,
        QirabotTimeoutError,
        MissingDependencyError,
    ])
    def test_subclass_of_exception(self, cls):
        assert issubclass(cls, Exception)

    def test_missing_dependency_is_import_error(self):
        assert issubclass(MissingDependencyError, ImportError)


class TestRemovedExceptions:
    """The cloud-era exceptions are gone in v3.2 — no billing, no Qirabot
    server, no server-side task state to report."""

    @pytest.mark.parametrize("name", [
        "InsufficientBalanceError",
        "RateLimitError",
        "QirabotConnectionError",
        "TaskTerminatedError",
    ])
    def test_not_exported(self, name):
        import qirabot
        import qirabot.exceptions

        assert not hasattr(qirabot.exceptions, name)
        assert not hasattr(qirabot, name)
        assert name not in qirabot.__all__
