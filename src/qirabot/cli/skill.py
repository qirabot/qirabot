"""`qirabot skill` — install the bundled Agent Skill into an agent's skills dir.

Claude Code users should prefer the plugin marketplace (auto-updates); this
command exists for every Agent-Skills-compatible tool that has no marketplace
(Codex, Cursor, Gemini CLI, …) and pins the skill to the installed SDK version,
so the API reference the agent reads always matches the `qirabot` it runs.

The payload ships inside the wheel at qirabot/skill-data/ — a mirror of
plugins/qirabot/skills/qirabot/ maintained by scripts/sync_skill.py.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import TYPE_CHECKING

import click

if TYPE_CHECKING:
    from importlib.abc import Traversable

# Written into the installed copy so upgrade/uninstall can tell "we put this
# here" apart from a directory the user created or hand-edited.
_MARKER = ".qirabot-skill.json"


@dataclass(frozen=True)
class AgentTarget:
    name: str
    label: str
    base: str  # skills dir relative to $HOME (user level) or cwd (--project)
    note: str = ""  # printed after a successful install


AGENTS: dict[str, AgentTarget] = {
    "agents": AgentTarget(
        "agents",
        "Agent Skills standard (.agents/skills — Codex, Cursor, Gemini CLI, …)",
        ".agents/skills",
    ),
    "claude": AgentTarget(
        "claude",
        "Claude Code",
        ".claude/skills",
        note=(
            "Note: for Claude Code the recommended install is the plugin marketplace\n"
            "(/plugin marketplace add qirabot/claude-plugins, then /plugin install\n"
            "qirabot@qirabot) — it auto-updates. This copy is pinned to the installed\n"
            "qirabot version; rerun `qirabot skill install claude` after upgrading."
        ),
    ),
    "codex": AgentTarget("codex", "OpenAI Codex", ".codex/skills"),
    "cursor": AgentTarget("cursor", "Cursor", ".cursor/skills"),
}


def _home() -> Path:
    """Seam for tests; Path.home() reads HOME/USERPROFILE at call time."""
    return Path.home()


def _payload() -> Traversable:
    payload = resources.files("qirabot").joinpath("skill-data")
    if not payload.is_dir():
        raise click.ClickException(
            "bundled skill payload not found — this qirabot install looks broken; "
            "try reinstalling (uv tool install --force qirabot)"
        )
    return payload


def _copy_tree(src: Traversable, dest: Path) -> None:
    """Traversable-based copy: works on every supported Python (resources.as_file
    only handles directories from 3.12) and even if the package is zip-imported."""
    dest.mkdir(parents=True, exist_ok=True)
    for entry in src.iterdir():
        target = dest / entry.name
        if entry.is_dir():
            _copy_tree(entry, target)
        else:
            target.write_bytes(entry.read_bytes())


def _resolve_skills_dir(agent: str | None, dir_: str | None, project: bool) -> Path:
    """The skills directory the `qirabot/` skill folder goes into."""
    if agent and dir_:
        raise click.UsageError("pass either AGENT or --dir, not both.")
    if dir_:
        if project:
            raise click.UsageError("--project only applies to a named AGENT, not --dir.")
        return Path(dir_)
    if agent:
        root = Path.cwd() if project else _home()
        return root / AGENTS[agent].base
    # No target: don't guess or prompt (keep it scriptable) — point at what we
    # can see on this machine instead.
    detected = [
        name for name, t in AGENTS.items() if (_home() / Path(t.base).parts[0]).is_dir()
    ]
    click.echo(
        "Error: specify a target — `qirabot skill install <agent>` or --dir PATH.\n"
        f"Agents: {', '.join(AGENTS)}",
        err=True,
    )
    if detected:
        click.echo(
            f"Detected on this machine: {', '.join(detected)} — "
            f"try: qirabot skill install {detected[0]}",
            err=True,
        )
    sys.exit(1)


def _installed_version(dest: Path) -> str | None:
    """Version from our marker file, or None if absent/unreadable."""
    try:
        data = json.loads((dest / _MARKER).read_text(encoding="utf-8"))
        version = data.get("version")
        return version if isinstance(version, str) else None
    except (OSError, ValueError):
        return None


@click.group()
def skill() -> None:
    """Install the bundled Agent Skill into an AI agent (Codex, Cursor, Claude Code, …).

    The skill teaches an agent to drive Qirabot end to end (preflight, API
    reference, starter templates) and is pinned to this SDK version.
    """


@skill.command("install")
@click.argument("agent", required=False, type=click.Choice(list(AGENTS)))
@click.option(
    "--dir", "dir_", type=click.Path(file_okay=False),
    help="Install into this skills directory (any Agent-Skills-compatible tool)",
)
@click.option(
    "--project", is_flag=True,
    help="Install into the project-level skills dir under the current directory instead of the user-level one",
)
@click.option("--force", "-f", is_flag=True, help="Overwrite an existing skill directory that this command did not install")
def install(agent: str | None, dir_: str | None, project: bool, force: bool) -> None:
    """Copy the qirabot skill into AGENT's skills directory (or --dir).

    Reinstalling after a `uv tool upgrade qirabot` upgrades the copy in place;
    a same-version rerun is a no-op. A directory not created by this command
    is never overwritten without --force.
    """
    payload = _payload()
    dest = _resolve_skills_dir(agent, dir_, project) / "qirabot"
    version = metadata.version("qirabot")

    if dest.exists():
        installed = _installed_version(dest)
        if installed == version and not force:
            click.echo(f"Already installed (v{version}) at {dest}. Pass --force to reinstall.")
            return
        if installed is None and not force:
            click.echo(
                f"Error: {dest} already exists and was not installed by this command. "
                "Pass --force to overwrite.",
                err=True,
            )
            sys.exit(1)
        if installed and installed != version:
            click.echo(f"Upgrading v{installed} -> v{version}")
        shutil.rmtree(dest)

    _copy_tree(payload, dest)
    (dest / _MARKER).write_text(
        json.dumps({"version": version, "installed_by": "qirabot skill install"}) + "\n",
        encoding="utf-8",
    )
    click.echo(f"Installed qirabot skill v{version} to {dest}")
    if agent and AGENTS[agent].note:
        click.echo(AGENTS[agent].note)
    click.echo("Restart your agent session to pick up the skill.")


@skill.command("uninstall")
@click.argument("agent", required=False, type=click.Choice(list(AGENTS)))
@click.option("--dir", "dir_", type=click.Path(file_okay=False), help="Skills directory to remove the skill from")
@click.option("--project", is_flag=True, help="Remove from the project-level skills dir instead of the user-level one")
@click.option("--force", "-f", is_flag=True, help="Remove even if the directory was not installed by this command")
def uninstall(agent: str | None, dir_: str | None, project: bool, force: bool) -> None:
    """Remove the qirabot skill from AGENT's skills directory (or --dir)."""
    dest = _resolve_skills_dir(agent, dir_, project) / "qirabot"
    if not dest.exists():
        click.echo(f"Not installed at {dest}")
        return
    if _installed_version(dest) is None and not force:
        click.echo(
            f"Error: {dest} was not installed by this command. Pass --force to remove it anyway.",
            err=True,
        )
        sys.exit(1)
    shutil.rmtree(dest)
    click.echo(f"Removed {dest}")


@skill.command("list")
def list_() -> None:
    """Show known agent skills directories and the installed skill version."""
    from rich.console import Console
    from rich.table import Table

    table = Table()
    table.add_column("Agent")
    table.add_column("Skills directory")
    table.add_column("Installed")
    for target in AGENTS.values():
        rows = [("user", _home() / target.base), ("project", Path.cwd() / target.base)]
        for scope, skills_dir in rows:
            installed = _installed_version(skills_dir / "qirabot")
            if scope == "project" and installed is None:
                continue  # only user-level rows by default; project rows when present
            table.add_row(
                f"{target.name} ({scope})" if scope == "project" else target.name,
                str(skills_dir),
                f"v{installed}" if installed else "-",
            )
    Console().print(table)
