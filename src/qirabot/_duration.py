"""Duration parsing/formatting shared by the SDK (max_duration) and the CLI
(--max-duration).

Accepted input: a plain number of seconds ("300", "1.5") or h/m/s components
in that order, each optional ("90s", "5m", "1h30m", "1h5m30s", "2.5m").
Output: the compact form back ("5m", "5m03s", "1h30m", "45s", "0.5s").
"""

from __future__ import annotations

import math
import re

_NUM = r"(\d+(?:\.\d+)?)"
_COMPONENTS = re.compile(rf"^(?:{_NUM}h)?(?:{_NUM}m)?(?:{_NUM}s)?$", re.IGNORECASE)


def parse_duration(text: str) -> float:
    """Seconds for ``text``; raises ValueError for anything malformed or not
    positive (a zero or negative time budget is never what was meant)."""
    s = text.strip()
    try:
        seconds = float(s)
    except ValueError:
        m = _COMPONENTS.match(s)
        # The regex also matches "" (every component optional).
        if not s or m is None:
            raise ValueError(
                f"invalid duration {text!r}: use seconds (300) or h/m/s (90s, 5m, 1h30m)"
            ) from None
        h, mi, sec = (float(g) if g else 0.0 for g in m.groups())
        seconds = h * 3600 + mi * 60 + sec
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(f"invalid duration {text!r}: must be greater than zero")
    return seconds


def format_duration(seconds: float) -> str:
    """Compact display form: whole seconds once a minute is reached (the
    sub-second part is noise there), one decimal below ten seconds."""
    if seconds < 10 and seconds != int(seconds):
        return f"{seconds:.1f}".rstrip("0").rstrip(".") + "s"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h" + (f"{m:02d}m" if m or s else "") + (f"{s:02d}s" if s else "")
    if m:
        return f"{m}m" + (f"{s:02d}s" if s else "")
    return f"{s}s"
