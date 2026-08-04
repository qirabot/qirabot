"""Tests for exception classes."""

import pytest

from qirabot.exceptions import (
    ActionError,
    AuthenticationError,
    InsufficientBalanceError,
    QirabotConnectionError,
    QirabotError,
    QirabotTimeoutError,
    RateLimitError,
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
    # InsufficientBalanceError / RateLimitError / QirabotConnectionError are
    # deprecated and never raised, but they remain public API until the next
    # major — so their hierarchy stays covered.
    @pytest.mark.parametrize("cls", [
        AuthenticationError,
        InsufficientBalanceError,
        RateLimitError,
        ActionError,
        QirabotTimeoutError,
        QirabotConnectionError,
    ])
    def test_subclass_of_qirabot_error(self, cls):
        assert issubclass(cls, QirabotError)

    @pytest.mark.parametrize("cls", [
        AuthenticationError,
        InsufficientBalanceError,
        RateLimitError,
        ActionError,
        QirabotTimeoutError,
        QirabotConnectionError,
    ])
    def test_subclass_of_exception(self, cls):
        assert issubclass(cls, Exception)
