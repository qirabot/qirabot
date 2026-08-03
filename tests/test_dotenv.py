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
        monkeypatch.delenv("QIRA_API_KEY", raising=False)
        path = _write(tmp_path, "QIRA_API_KEY=qk_123\n")
        assert load_dotenv(path) is True
        assert os.environ["QIRA_API_KEY"] == "qk_123"

    def test_real_env_wins_by_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QIRA_API_KEY", "real")
        path = _write(tmp_path, "QIRA_API_KEY=fromfile\n")
        load_dotenv(path)
        assert os.environ["QIRA_API_KEY"] == "real"  # not overridden

    def test_override_replaces(self, tmp_path, monkeypatch):
        monkeypatch.setenv("QIRA_API_KEY", "real")
        path = _write(tmp_path, "QIRA_API_KEY=fromfile\n")
        load_dotenv(path, override=True)
        assert os.environ["QIRA_API_KEY"] == "fromfile"

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
        monkeypatch.delenv("QIRA_BASE_URL", raising=False)
        path = _write(tmp_path, "QIRA_BASE_URL=https://example.test\n")
        monkeypatch.setenv("QIRA_DOTENV", path)
        assert load_dotenv() is True  # picks up $QIRA_DOTENV
        assert os.environ["QIRA_BASE_URL"] == "https://example.test"


class TestInjectedFrom:
    """load_dotenv records which keys it injected (and from which file), so
    error messages can name the concrete source of a leftover variable."""

    def _fresh(self, monkeypatch):
        import qirabot._dotenv as dotenv_mod

        monkeypatch.setattr(dotenv_mod, "_injected", {})
        return dotenv_mod

    def test_injected_key_reports_its_file(self, tmp_path, monkeypatch):
        dotenv_mod = self._fresh(monkeypatch)
        monkeypatch.delenv("SOME_KEY", raising=False)
        path = _write(tmp_path, "SOME_KEY=v\n")
        load_dotenv(path)
        assert dotenv_mod.injected_from("SOME_KEY") == path

    def test_shadowed_key_is_not_recorded(self, tmp_path, monkeypatch):
        # A real exported variable wins over the .env entry, so the value in
        # os.environ did NOT come from the file — injected_from must say so.
        dotenv_mod = self._fresh(monkeypatch)
        monkeypatch.setenv("SOME_KEY", "real")
        path = _write(tmp_path, "SOME_KEY=fromfile\n")
        load_dotenv(path)
        assert dotenv_mod.injected_from("SOME_KEY") == ""

    def test_unknown_key_is_empty(self, monkeypatch):
        dotenv_mod = self._fresh(monkeypatch)
        assert dotenv_mod.injected_from("NEVER_SET") == ""
