"""Tests for `qirabot skill install/uninstall/list` (filesystem only, no network)."""

from __future__ import annotations

import json
from importlib import metadata

import pytest
from click.testing import CliRunner

from qirabot.cli import skill as skill_mod
from qirabot.cli.main import cli

VERSION = metadata.version("qirabot")

# Files whose presence proves the whole payload copied (one per subdirectory).
PAYLOAD_SENTINELS = (
    "SKILL.md",
    "references/REFERENCE.md",
    "references/CLI.md",
    "scripts/preflight.py",
    "templates/browser.py",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(skill_mod, "_home", lambda: tmp_path)
    return tmp_path


def _invoke(args, runner=None):
    return (runner or CliRunner()).invoke(cli, ["skill", *args])


def test_install_dir_copies_payload_and_marker(tmp_path):
    result = _invoke(["install", "--dir", str(tmp_path / "skills")])
    assert result.exit_code == 0, result.output

    dest = tmp_path / "skills" / "qirabot"
    for rel in PAYLOAD_SENTINELS:
        assert (dest / rel).is_file(), rel
    marker = json.loads((dest / ".qirabot-skill.json").read_text())
    assert marker["version"] == VERSION
    assert str(dest) in result.output


def test_install_claude_targets_home_and_steers_to_marketplace(home):
    result = _invoke(["install", "claude"])
    assert result.exit_code == 0, result.output
    assert (home / ".claude" / "skills" / "qirabot" / "SKILL.md").is_file()
    assert "plugin marketplace" in result.output


def test_install_project_lands_under_cwd(home):
    runner = CliRunner()
    with runner.isolated_filesystem() as fs:
        result = _invoke(["install", "agents", "--project"], runner)
        assert result.exit_code == 0, result.output
        assert (home / ".agents").exists() is False
        import pathlib

        assert (pathlib.Path(fs) / ".agents" / "skills" / "qirabot" / "SKILL.md").is_file()


def test_reinstall_same_version_is_a_noop(home):
    assert _invoke(["install", "codex"]).exit_code == 0
    result = _invoke(["install", "codex"])
    assert result.exit_code == 0, result.output
    assert "Already installed" in result.output


def test_reinstall_other_version_upgrades_without_force(home):
    assert _invoke(["install", "codex"]).exit_code == 0
    dest = home / ".codex" / "skills" / "qirabot"
    (dest / ".qirabot-skill.json").write_text(
        json.dumps({"version": "0.0.1", "installed_by": "qirabot skill install"})
    )
    (dest / "SKILL.md").write_text("stale")

    result = _invoke(["install", "codex"])
    assert result.exit_code == 0, result.output
    assert "Upgrading v0.0.1" in result.output
    assert (dest / "SKILL.md").read_text() != "stale"
    assert json.loads((dest / ".qirabot-skill.json").read_text())["version"] == VERSION


def test_install_refuses_foreign_dir_without_force(tmp_path):
    dest = tmp_path / "skills" / "qirabot"
    dest.mkdir(parents=True)
    (dest / "SKILL.md").write_text("the user's own skill")

    result = _invoke(["install", "--dir", str(tmp_path / "skills")])
    assert result.exit_code == 1
    assert "--force" in result.output

    forced = _invoke(["install", "--dir", str(tmp_path / "skills"), "--force"])
    assert forced.exit_code == 0, forced.output
    assert (dest / "references" / "REFERENCE.md").is_file()


def test_agent_and_dir_are_mutually_exclusive(tmp_path):
    result = _invoke(["install", "claude", "--dir", str(tmp_path)])
    assert result.exit_code == 2
    assert "not both" in result.output


def test_project_requires_named_agent(tmp_path):
    result = _invoke(["install", "--dir", str(tmp_path), "--project"])
    assert result.exit_code == 2


def test_no_target_errors_with_detection_hint(home):
    (home / ".claude").mkdir()
    result = _invoke(["install"])
    assert result.exit_code == 1
    assert "specify a target" in result.output
    assert "qirabot skill install claude" in result.output


def test_uninstall_marked_missing_and_foreign(home, tmp_path):
    assert _invoke(["install", "cursor"]).exit_code == 0
    dest = home / ".cursor" / "skills" / "qirabot"

    result = _invoke(["uninstall", "cursor"])
    assert result.exit_code == 0, result.output
    assert not dest.exists()

    again = _invoke(["uninstall", "cursor"])
    assert again.exit_code == 0
    assert "Not installed" in again.output

    foreign = tmp_path / "skills" / "qirabot"
    foreign.mkdir(parents=True)
    refuse = _invoke(["uninstall", "--dir", str(tmp_path / "skills")])
    assert refuse.exit_code == 1
    assert "--force" in refuse.output
    forced = _invoke(["uninstall", "--dir", str(tmp_path / "skills"), "--force"])
    assert forced.exit_code == 0
    assert not foreign.exists()


def test_list_shows_installed_version(home):
    assert _invoke(["install", "claude"]).exit_code == 0
    result = _invoke(["list"])
    assert result.exit_code == 0, result.output
    assert "claude" in result.output
    assert VERSION in result.output
