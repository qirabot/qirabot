"""Tests for the standalone .env loader (qirabot.load_dotenv)."""

import os

from qirabot import load_dotenv


def _write(tmp_path, body):
    p = tmp_path / ".env"
    p.write_text(body, encoding="utf-8")
    return str(p)


class TestLoadDotenv:
    def test_missing_file_is_noop(self, tmp_path):
        assert load_dotenv(str(tmp_path / "nope.env")) is False

    def test_basic_key_value(self, tmp_path, monkeypatch):
        monkeypatch.delenv("QIRA_MODEL", raising=False)
        path = _write(tmp_path, "QIRA_MODEL=gemini-vertex/gemini-3.8-flash\n")
        assert load_dotenv(path) is True
        assert os.environ["QIRA_MODEL"] == "gemini-vertex/gemini-3.8-flash"

    def test_real_env_wins_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QIRA_MODEL", "real")
        path = _write(tmp_path, "QIRA_MODEL=fromfile\n")
        load_dotenv(path)
        assert os.environ["QIRA_MODEL"] == "real"  # not overridden

    def test_override_replaces(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QIRA_MODEL", "real")
        path = _write(tmp_path, "QIRA_MODEL=fromfile\n")
        load_dotenv(path, override=True)
        assert os.environ["QIRA_MODEL"] == "fromfile"

    def test_comments_blanks_quotes_and_export(self, tmp_path, monkeypatch):
        for k in ("A", "B", "C"):
            monkeypatch.delenv(k, raising=False)
        path = _write(
            tmp_path,
            "# a comment\n\nexport A=1\nB=\"two\"\nC='three'\nMALFORMED\n",
        )
        load_dotenv(path)
        assert os.environ["A"] == "1"
        assert os.environ["B"] == "two"
        assert os.environ["C"] == "three"

    def test_path_env_override(self, tmp_path, monkeypatch):
        monkeypatch.delenv("QIRA_REPORT_DIR", raising=False)
        path = _write(tmp_path, "QIRA_REPORT_DIR=/tmp/reports\n")
        monkeypatch.setenv("QIRA_DOTENV", path)
        assert load_dotenv() is True  # picks up $QIRA_DOTENV
        assert os.environ["QIRA_REPORT_DIR"] == "/tmp/reports"
