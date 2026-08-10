"""Tests for `qirabot install-browser` and Vertex configuration wiring.

v3 dropped the cloud backend: `qirabot login` and the API-key resolution
layers are gone. The whole credential story is now Google Cloud ADC plus the
--vertex-project/--vertex-location globals (env: QIRA_VERTEX_PROJECT /
QIRA_VERTEX_LOCATION), which _make_bot threads into Qirabot().
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner


def _invoke(args):
    from qirabot.cli.main import cli

    return CliRunner().invoke(cli, args)


# ---------------------------------------------------------------------------
# Vertex project/location/model wiring: CLI -> _make_bot -> Qirabot(...)
# ---------------------------------------------------------------------------


@pytest.fixture
def qirabot_kwargs(monkeypatch):
    """Capture the kwargs the real _make_bot hands to Qirabot().

    The Qirabot class itself is replaced (the constructor would otherwise
    build the engine), and _run_local is stubbed so the browser command stops
    after construction.
    """
    import qirabot
    from qirabot.cli import main

    captured = {}

    def fake_qirabot(**kwargs):
        captured.update(kwargs)
        return MagicMock(name="bot")

    monkeypatch.setattr(qirabot, "Qirabot", fake_qirabot)
    monkeypatch.setattr(main, "_run_local", lambda *a, **k: None)
    return captured


class TestVertexConfigWiring:
    def test_flags_reach_qirabot(self, qirabot_kwargs):
        result = _invoke([
            "--vertex-project", "flag-proj",
            "--vertex-location", "europe-west1",
            "browser", "do it",
        ])

        assert result.exit_code == 0, result.output
        assert qirabot_kwargs["vertex_project"] == "flag-proj"
        assert qirabot_kwargs["vertex_location"] == "europe-west1"

    def test_env_vars_are_the_fallback(self, qirabot_kwargs, monkeypatch):
        monkeypatch.setenv("QIRA_VERTEX_PROJECT", "env-proj")
        monkeypatch.setenv("QIRA_VERTEX_LOCATION", "us-central1")

        result = _invoke(["browser", "do it"])

        assert result.exit_code == 0, result.output
        assert qirabot_kwargs["vertex_project"] == "env-proj"
        assert qirabot_kwargs["vertex_location"] == "us-central1"

    def test_flag_beats_env(self, qirabot_kwargs, monkeypatch):
        monkeypatch.setenv("QIRA_VERTEX_PROJECT", "env-proj")

        result = _invoke(["--vertex-project", "flag-proj", "browser", "do it"])

        assert result.exit_code == 0, result.output
        assert qirabot_kwargs["vertex_project"] == "flag-proj"

    def test_unset_stays_empty_for_engine_resolution(self, qirabot_kwargs):
        """No flag, no env: the CLI passes "" through so the engine's own
        chain (GOOGLE_CLOUD_PROJECT, the ADC credentials' project) decides."""
        result = _invoke(["browser", "do it"])

        assert result.exit_code == 0, result.output
        assert qirabot_kwargs["vertex_project"] == ""
        assert qirabot_kwargs["vertex_location"] == ""

    def test_model_flag_reaches_qirabot(self, qirabot_kwargs):
        result = _invoke([
            "browser", "do it", "-m", "gemini-vertex/gemini-3.6-pro",
        ])

        assert result.exit_code == 0, result.output
        assert qirabot_kwargs["model"] == "gemini-vertex/gemini-3.6-pro"

    def test_construction_failure_prints_engine_error(self, monkeypatch):
        """Engine construction errors (missing ADC / unknown provider /
        missing project) surface as a one-line Error and exit 1."""
        import qirabot

        def boom(**kwargs):
            raise ValueError(
                "no Google Cloud project configured; pass vertex_project=, set "
                "QIRA_VERTEX_PROJECT / GOOGLE_CLOUD_PROJECT, or use credentials "
                "that carry a project id"
            )

        monkeypatch.setattr(qirabot, "Qirabot", boom)

        result = _invoke(["browser", "do it"])

        assert result.exit_code == 1
        assert "no Google Cloud project configured" in result.output


# ---------------------------------------------------------------------------
# install-browser
# ---------------------------------------------------------------------------


class TestInstallBrowser:
    def test_missing_extra_raises_install_hint(self, monkeypatch):
        from qirabot.cli import main as cli_main
        from qirabot.exceptions import MissingDependencyError

        def missing(module, extra=None):
            raise MissingDependencyError(
                'Install it with:  uv pip install "qirabot[browser]"'
            )

        monkeypatch.setattr(cli_main, "require", missing)
        result = CliRunner().invoke(cli_main.cli, ["install-browser"])
        assert result.exit_code != 0
        assert "qirabot[browser]" in str(result.exception)

    def test_delegates_to_playwright_module(self, monkeypatch):
        from qirabot.cli import main as cli_main

        monkeypatch.setattr(cli_main, "require", lambda m, e=None: MagicMock())
        calls = {}

        def fake_run(cmd, **kwargs):
            calls["cmd"] = cmd
            return MagicMock(returncode=0)

        monkeypatch.setattr("subprocess.run", fake_run)
        result = CliRunner().invoke(cli_main.cli, ["install-browser"])
        assert result.exit_code == 0, result.output
        assert calls["cmd"] == [sys.executable, "-m", "playwright", "install", "chromium"]
        assert "ready" in result.output

    def test_nonzero_exit_becomes_error(self, monkeypatch):
        from qirabot.cli import main as cli_main

        monkeypatch.setattr(cli_main, "require", lambda m, e=None: MagicMock())
        monkeypatch.setattr("subprocess.run", lambda *a, **k: MagicMock(returncode=3))
        result = CliRunner().invoke(cli_main.cli, ["install-browser"])
        assert result.exit_code != 0
        assert "exit 3" in result.output
