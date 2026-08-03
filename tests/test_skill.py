"""Drift guard for the bundled Agent Skill (``plugins/qirabot/skills/qirabot/``).

The skill ships copy-paste code: the snippets in ``references/REFERENCE.md`` and
the ``templates/*.py`` an agent runs verbatim. If a public method is renamed or a
constructor kwarg is dropped from the SDK, that code silently breaks — and the
only signal is a failed automation run, far from this repo. These tests tie the
skill's code back to the live API so drift fails CI here instead.

No network and no devices: only signatures are inspected (no ``Qirabot`` is
ever constructed), and the templates are *parsed*, never executed, so nothing
launches a browser, emulator, or desktop session.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

import qirabot
from qirabot import Qirabot
from qirabot.bound import _BoundQirabot

SKILL = (
    Path(__file__).resolve().parent.parent / "plugins" / "qirabot" / "skills" / "qirabot"
)
REFERENCE = SKILL / "references" / "REFERENCE.md"
TEMPLATES = sorted((SKILL / "templates").glob("*.py"))

# A snippet may call a method on the unbound client (``bot.click(page, ...)``) or,
# after ``bind()``, on the bound proxy (``bot.click(...)``). Either is valid.
PUBLIC_METHODS = {
    name
    for cls in (Qirabot, _BoundQirabot)
    for name in dir(cls)
    if not name.startswith("_") and callable(getattr(cls, name, None))
}
CTOR_PARAMS = set(inspect.signature(Qirabot.__init__).parameters) - {"self"}

# The API backbone an agent relies on. Auto-discovery below catches whatever the
# docs happen to mention; this curated set fails loudly if a core method is
# renamed even when no snippet currently calls it (e.g. ``bind`` is used as a
# chained ``Qirabot().bind(...)`` and so isn't picked up as ``bot.<method>(``).
CORE_METHODS = {
    "ai", "click", "type_text", "extract", "verify", "wait_for",
    "bind", "current_page", "open", "close", "screenshot",
}

_CALL_RE = re.compile(r"\bbot\.([a-z_]+)\s*\(")
_CTOR_RE = re.compile(r"\bQirabot\(([^)]*)\)", re.S)
_FROM_IMPORT_RE = re.compile(
    r"from qirabot import \(([^)]*)\)|from qirabot import ([^\n(]+)"
)


def _methods_called(source: str) -> set[str]:
    return set(_CALL_RE.findall(source))


def _ctor_kwargs(source: str) -> set[str]:
    kwargs: set[str] = set()
    for body in _CTOR_RE.findall(source):
        kwargs.update(re.findall(r"(\w+)\s*=", body))
    return kwargs


def _imported_names(source: str) -> set[str]:
    names: set[str] = set()
    for grouped, flat in _FROM_IMPORT_RE.findall(source):
        for chunk in (grouped or flat).split(","):
            name = chunk.strip()
            if name:
                names.add(name)
    return names


def _reference_constructor_options() -> set[str]:
    """Kwarg names from the 'Common constructor options' table's first column.

    The table is the most drift-prone part of the reference. Each row's first
    cell holds the option name(s) in backticks (sometimes alongside a ``QIRA_*``
    env var); we keep the lowercase, non-env tokens.
    """
    options: set[str] = set()
    in_table = False
    for raw in REFERENCE.read_text(encoding="utf-8").splitlines():
        ln = raw.strip()
        if ln.startswith("| Option"):
            in_table = True
            continue
        if in_table:
            if not ln.startswith("|"):
                break  # table ended
            if set(ln) <= {"|", "-", " ", ":"}:
                continue  # the |---|---| separator row
            first_cell = ln.strip("|").split("|", 1)[0]
            for tok in re.findall(r"`([^`]+)`", first_cell):
                if re.fullmatch(r"[a-z][a-z0-9_]*", tok) and not tok.startswith("qira_"):
                    options.add(tok)
    return options


def test_skill_assets_present():
    assert REFERENCE.is_file(), "REFERENCE.md missing"
    assert TEMPLATES, "no templates under plugins/qirabot/skills/qirabot/templates/"


def test_core_methods_exist_on_sdk():
    missing = CORE_METHODS - PUBLIC_METHODS
    assert not missing, f"skill assumes methods the SDK no longer has: {sorted(missing)}"


def test_reference_methods_exist():
    text = REFERENCE.read_text(encoding="utf-8")
    called = _methods_called(text)
    # Guard against a refactor that empties the reference (vacuous pass).
    assert {"ai", "click", "extract", "verify"} <= called, (
        "REFERENCE.md no longer documents the core actions"
    )
    unknown = called - PUBLIC_METHODS
    assert not unknown, f"REFERENCE.md calls non-existent bot methods: {sorted(unknown)}"


def test_reference_constructor_options_valid():
    options = _reference_constructor_options()
    assert options, "constructor options table not found / parsed"
    unknown = options - CTOR_PARAMS
    assert not unknown, (
        f"REFERENCE.md documents constructor options the SDK lacks: {sorted(unknown)}"
    )


def test_reference_imports_resolve():
    for name in _imported_names(REFERENCE.read_text(encoding="utf-8")):
        assert hasattr(qirabot, name), f"REFERENCE.md imports unknown name: {name}"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_parses(template: Path):
    ast.parse(template.read_text(encoding="utf-8"))  # raises SyntaxError on drift


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_methods_exist(template: Path):
    unknown = _methods_called(template.read_text(encoding="utf-8")) - PUBLIC_METHODS
    assert not unknown, f"{template.name} calls non-existent bot methods: {sorted(unknown)}"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_constructor_kwargs_valid(template: Path):
    unknown = _ctor_kwargs(template.read_text(encoding="utf-8")) - CTOR_PARAMS
    assert not unknown, f"{template.name} passes unknown Qirabot() kwargs: {sorted(unknown)}"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_template_imports_resolve(template: Path):
    tree = ast.parse(template.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "qirabot":
            for alias in node.names:
                assert hasattr(qirabot, alias.name), (
                    f"{template.name} imports unknown qirabot.{alias.name}"
                )
