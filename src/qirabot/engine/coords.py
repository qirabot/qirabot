"""Coordinate rescaling shared by inline grounding and single-step locate.

Mirrors internal/decision/coords.go, plus the bbox center conversion that Go
keeps in locate.go.
"""

from __future__ import annotations

from typing import Any


def rescale_point(raw_x: Any, raw_y: Any, w: int, h: int) -> tuple[int, int, str]:
    """Convert a model-emitted coordinate pair into pixels of a w×h frame.

    mode: "normalized" (both axes 0–1000 → scaled), "pixel" (BOTH axes >1000
    yet within the frame bounds → an unambiguous raw-pixel emission, used
    as-is), "" (unusable). A single axis >1000 is ambiguous (a pixel value, or
    a normalized overshoot whose partner axis is still normalized) and is left
    unusable so the caller re-decides rather than guessing and mis-clicking.
    """
    if w <= 0 or h <= 0:
        return 0, 0, ""
    nx = to_float(raw_x)
    ny = to_float(raw_y)
    if nx is None or ny is None or nx < 0 or ny < 0:
        return 0, 0, ""
    if nx <= 1000 and ny <= 1000:
        return _round_half_away(nx / 1000 * w), _round_half_away(ny / 1000 * h), "normalized"
    # Neither axis can be normalized (both >1000); if both fit the frame, the
    # model emitted raw pixels — take them as-is.
    if nx > 1000 and ny > 1000 and nx <= float(w) and ny <= float(h):
        return _round_half_away(nx), _round_half_away(ny), "pixel"
    return 0, 0, ""


def bbox_center(vals: list[float], w: int, h: int) -> tuple[int, int]:
    """Center point of a [ymin, xmin, ymax, xmax] box normalized to 0–1000."""
    ymin, xmin, ymax, xmax = vals[0], vals[1], vals[2], vals[3]
    return (
        _round_half_away((xmin + xmax) / 2 / 1000 * w),
        _round_half_away((ymin + ymax) / 2 / 1000 * h),
    )


def to_float(v: Any) -> float | None:
    """Coerce a JSON-decoded number to float. bool is excluded: it is an int
    subclass in Python but never a coordinate."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _round_half_away(x: float) -> int:
    """math.Round semantics: halves round away from zero (Python's built-in
    round() is banker's rounding and would differ on exact .5 values)."""
    if x >= 0:
        return int(x + 0.5)
    return -int(-x + 0.5)
