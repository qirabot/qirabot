"""Tests for the duration helpers behind max_duration / --max-duration."""

import pytest

from qirabot._duration import format_duration, parse_duration


class TestParseDuration:
    @pytest.mark.parametrize("text, seconds", [
        ("300", 300), ("1.5", 1.5), ("90s", 90), ("5m", 300), ("1h", 3600),
        ("1h30m", 5400), ("1h5m30s", 3930), ("2.5m", 150), (" 5M ", 300),
        ("10m30s", 630),
    ])
    def test_valid(self, text, seconds):
        assert parse_duration(text) == seconds

    @pytest.mark.parametrize("text", [
        "", "  ", "abc", "5x", "m", "30s5m", "5 m", "-5", "0", "0s", "1h-5m", "inf", "nan",
    ])
    def test_invalid(self, text):
        with pytest.raises(ValueError):
            parse_duration(text)


class TestFormatDuration:
    @pytest.mark.parametrize("seconds, text", [
        (0, "0s"), (0.5, "0.5s"), (45, "45s"), (45.4, "45s"), (60, "1m"),
        (90, "1m30s"), (303, "5m03s"), (300, "5m"), (3600, "1h"),
        (5400, "1h30m"), (3661, "1h01m01s"), (3605, "1h00m05s"),
    ])
    def test_format(self, seconds, text):
        assert format_duration(seconds) == text

    @pytest.mark.parametrize("text", ["300", "90s", "1h30m", "1h5m30s"])
    def test_round_trip(self, text):
        assert parse_duration(format_duration(parse_duration(text))) == parse_duration(text)
