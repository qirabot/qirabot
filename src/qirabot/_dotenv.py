"""Best-effort ``.env`` loader (standard library only).

A small helper so an automation *script* can keep settings like ``QIRA_API_KEY``
/ ``QIRA_BASE_URL`` in a project ``.env`` instead of exporting them by hand.
Following the ``python-dotenv`` convention, this is **never** run implicitly:
the SDK does not read ``.env`` on its own — the calling script opts in by calling
:func:`load_dotenv` (typically once, at the top). That keeps the library free of
hidden side effects on ``os.environ``.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("qirabot")

# Keys :func:`load_dotenv` actually injected into ``os.environ``, mapped to the
# file they came from. Lets error messages name the concrete source ("loaded
# from ./.env") instead of sending the user hunting through shell profiles for
# a variable they never exported.
_injected: dict[str, str] = {}


def injected_from(key: str) -> str:
    """The dotenv file path that set ``key``, or ``""`` if the current value
    did not come from :func:`load_dotenv` (exported variable, CI config, ...)."""
    return _injected.get(key, "")


def load_dotenv(path: str | None = None, *, override: bool = False) -> bool:
    """Load ``KEY=VALUE`` lines from ``path`` into ``os.environ``.

    ``path`` defaults to ``$QIRA_DOTENV`` or ``./.env``. Returns ``True`` if a
    file was read. Best-effort: a missing file is a no-op (returns ``False``) and
    a malformed line is skipped — it never raises. Existing ``os.environ`` keys
    are preserved unless ``override`` is set, so a real exported variable always
    wins. Supports ``#`` comments, an optional ``export`` prefix, and surrounding
    single/double quotes on the value.
    """
    if path is None:
        path = os.environ.get("QIRA_DOTENV") or ".env"
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return False
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if not key:
            continue
        value = value.strip().strip('"').strip("'")
        if override or key not in os.environ:
            os.environ[key] = value
            _injected[key] = path
    return True
