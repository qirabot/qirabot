"""Run timeline: the report's data model, accumulated as a session runs.

Owns the step log, per-section outcome bookkeeping, session usage totals and
screenshot persistence for one :class:`qirabot.Qirabot` session. Pure
bookkeeping plus disk writes — rendering lives in :mod:`qirabot.report`, and
the client facade only forwards into here.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from qirabot._annotate import render_step_images
from qirabot.adapters.base import ScreenshotConfig
from qirabot.engine.types import TokenUsage

logger = logging.getLogger("qirabot")


class RunTimeline:
    """Step log + section bookkeeping + usage totals for one client session.

    ``entries`` holds plain dict rows — exactly the schema
    :func:`qirabot.report.write_html` consumes (see :meth:`record_step` for
    the fields). Deliberately dicts, not dataclasses: the log is handed to
    ``write_html`` verbatim, whose public contract already takes dict rows.
    """

    def __init__(
        self, enabled: bool, report_dir: Path, screenshot_config: ScreenshotConfig
    ) -> None:
        self.enabled = enabled
        self.report_dir = report_dir
        self.screenshot_config = screenshot_config
        # Session-wide action timeline for the report.
        self.entries: list[dict[str, Any]] = []
        # The task section ai() runs are grouped under; standalone actions
        # carry the "setup" key, which the report renders as "manual".
        self.current_section = "setup"
        # instruction -> times ai() has run it, to give repeat runs a
        # numbered section key ("<instruction> #2") so each run keeps its own
        # outcome/error instead of the later run overwriting the earlier.
        self.section_runs: dict[str, int] = {}
        # ai() section key -> RunStatus, for the per-section badge in the
        # report (completed / goal_failed / max_steps / error / cancelled).
        self.section_outcomes: dict[str, str] = {}
        # ai() section key -> failure text, rendered as a banner above the
        # section's step table (max-steps truncation / run-ending error).
        self.section_errors: dict[str, str] = {}
        # Session-wide totals for the report header. Token/timing data rides
        # in each ai() step result, not in the entries, so it accumulates
        # here as steps run and is handed to the report at render time.
        self.stats: dict[str, int] = {
            "ai_steps": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "step_duration_ms": 0,
            "llm_decision_duration_ms": 0,
        }
        self._screenshot_counter = 0
        # Last frame written by record_step (report-relative path + thumbnail),
        # so a step that reuses the previous frame can point at it instead of
        # rendering a blank cell. Manual screenshot() frames are deliberately
        # not tracked here — they are not part of any step.
        self._last_shot = ""
        self._last_thumb = ""

    def begin_section(self, instruction: str) -> str:
        """Open the section for one ai() run and make it current.

        Repeat runs of the same instruction get a numbered key so their
        outcomes stay separate. Returns the section key; the caller restores
        the previous ``current_section`` when the run ends.
        """
        section = instruction or "ai"
        runs = self.section_runs.get(section, 0) + 1
        self.section_runs[section] = runs
        if runs > 1:
            section = f"{section} #{runs}"
        self.current_section = section
        return section

    def add_tokens(
        self, usage: TokenUsage, *, step_ms: int = 0, llm_ms: int = 0
    ) -> None:
        """Fold an engine call's token/timing usage into the run stats —
        failed calls included: a failed step's decide attempts (up to
        MAX_GROUNDING_ATTEMPTS of them) are real spend the engine reports on
        the error, and dropping them would understate the session totals
        exactly on the most expensive steps."""
        self.stats["input_tokens"] += usage.input_tokens
        self.stats["output_tokens"] += usage.output_tokens
        self.stats["thinking_tokens"] += usage.thinking_tokens
        self.stats["cache_read_tokens"] += usage.cache_read_tokens
        self.stats["cache_write_tokens"] += usage.cache_write_tokens
        self.stats["step_duration_ms"] += step_ms
        self.stats["llm_decision_duration_ms"] += llm_ms

    def add_step_usage(
        self, usage: TokenUsage, *, step_ms: int = 0, llm_ms: int = 0
    ) -> None:
        """A successful AI call: its usage plus one AI step.

        ai_steps counts committed calls only (matching the report's step
        count); failed calls go through :meth:`add_tokens` alone.
        """
        self.stats["ai_steps"] += 1
        self.add_tokens(usage, step_ms=step_ms, llm_ms=llm_ms)

    def save_frame(self, data: bytes, label: str) -> Path | None:
        """Write a full-resolution screenshot to ``report_dir/screenshots/``."""
        if not self.enabled:
            return None
        dir_path = self.report_dir / "screenshots"
        dir_path.mkdir(parents=True, exist_ok=True)
        self._screenshot_counter += 1
        filename = (
            f"{self._screenshot_counter:03d}_{label}.{self.screenshot_config.extension}"
        )
        path = dir_path / filename
        path.write_bytes(data)
        logger.debug("screenshot saved: %s", path)
        return path

    def record_step(
        self,
        data: bytes,
        action_type: str,
        params: dict[str, Any] | None,
        coords: tuple[float, float] | None = None,
        *,
        end_coords: tuple[float, float] | None = None,
        output: str = "",
        finished: bool = False,
        success: bool = True,
        warn: bool = False,
        decision: str = "",
        coord_scale: float = 1.0,
        reused_frame: bool = False,
    ) -> dict[str, Any] | None:
        """Save the screenshot (if reporting) and append a step to the timeline.

        Returns the appended log entry so the caller can backfill fields that
        only become known after recording (e.g. an action's execution result),
        or ``None`` when reporting is off and nothing was recorded.

        ``assert`` actions (verify / wait_for polls) are recorded like any
        other step: the poll frames are the key evidence when a ``wait_for``
        times out.

        ``reused_frame`` marks a step the caller decided on a frame captured
        for an earlier step (nothing moved the device in between). Such a step
        has no new picture of its own, so it reuses the previous frame's image
        rather than leaving a blank cell in the report — and is flagged so the
        report can say the frame predates the step.
        """
        # Reporting off → zero overhead.
        if not self.enabled:
            return None
        # A reused frame that marks no coordinates has nothing new to draw:
        # point at the file already on disk instead of writing a byte-identical
        # copy. With coordinates the annotation IS the new information, so it
        # gets its own rendered copy of the same picture.
        if reused_frame and not coords and not end_coords:
            shot, thumb = self._last_shot, self._last_thumb
        else:
            # Annotation + thumbnailing share a single PIL decode; never let a
            # malformed/unexpected screenshot break the actual action — degrade
            # to the raw bytes / no thumbnail instead.
            annotated = data
            thumb = ""
            if data:
                try:
                    annotated, thumb = render_step_images(
                        data,
                        coords,
                        self.screenshot_config,
                        end_coords=end_coords,
                        coord_scale=coord_scale,
                    )
                except Exception:
                    logger.debug("render step images failed", exc_info=True)
            frame = self.save_frame(annotated, action_type or "action") if data else None
            shot = f"screenshots/{frame.name}" if frame else ""
            self._last_shot, self._last_thumb = shot, thumb
        entry: dict[str, Any] = {
            "section": self.current_section,
            "ts": time.time(),
            "action_type": action_type or "",
            "params": params or {},
            "decision": decision or "",
            "output": output or "",
            "finished": bool(finished),
            "success": bool(success),
            "coords": list(coords) if coords else None,
            # relative to report_dir so the html can link it directly
            "screenshot": shot,
            "thumb": thumb,
        }
        # warn marks a truncation (max steps), not a failure — the report
        # renders it amber instead of red. Only set when true to keep the log
        # lean and older entries unchanged.
        if warn:
            entry["warn"] = True
        # Only set when true, same reason as warn.
        if reused_frame:
            entry["reused_frame"] = True
        self.entries.append(entry)
        return entry
