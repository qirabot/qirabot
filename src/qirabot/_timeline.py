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
from qirabot.engine.providers.stats import StatsSnapshot
from qirabot.engine.types import TokenUsage

logger = logging.getLogger("qirabot")

# Provider-ledger fields kept per section and per entry (see StatsSnapshot).
_LEDGER_KEYS = ("model_ms", "rate_limit_wait_ms", "outage_wait_ms", "timeout_wait_ms")
_SECTION_KEYS = ("step_duration_ms", *_LEDGER_KEYS)


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
        # section's step table (run-ending error; a max-steps truncation has
        # nothing to add to its badge, so it records none).
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
        }
        # ai() section key -> its full instruction, for the report's titles
        # (a repeat run's key carries a " #2" suffix the instruction lacks).
        self.instructions: dict[str, str] = {}
        # Section key -> that section's wall-clock time and provider-ledger
        # slice (model time, capacity waits), for the per-task rows. Every
        # AI call adds to the section that was current when it ran, failed
        # calls included; "setup" collects standalone calls.
        self.section_stats: dict[str, dict[str, int]] = {}
        # (model, tier) -> token counts, so the report can break spend down
        # by what each call is billed at. tier is "" when unknown (a failed
        # call carries no served tier).
        self.tokens_by: dict[tuple[str, str], dict[str, int]] = {}
        # (platform, width, height) of the first device an AI call ran on,
        # for the report's meta line; None until one has.
        self.device: tuple[str, int, int] | None = None
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
        self.instructions[section] = instruction
        return section

    def note_device(self, platform: str, width: int, height: int) -> None:
        """Remember the device an AI call ran on; the first one sticks."""
        if self.device is None:
            self.device = (platform, width, height)

    def record_outcome(self, status: str, error: str = "") -> None:
        """Record how the current section ended.

        ``status`` is a :data:`qirabot.client.RunStatus` value (a plain str
        here to keep this module import-light); the report renders it as the
        section's badge. ``error``, when non-empty, becomes the banner above
        the section's step table — and is left untouched otherwise, so a
        later status-only update cannot erase a reason already recorded.
        """
        self.section_outcomes[self.current_section] = status
        if error:
            self.section_errors[self.current_section] = error

    def add_tokens(self, usage: TokenUsage, model: str = "", tier: str = "") -> None:
        """Fold an engine call's token usage into the run stats — failed
        calls included: a failed step's decide attempts (up to
        MAX_GROUNDING_ATTEMPTS of them) are real spend the engine reports on
        the error, and dropping them would understate the session totals
        exactly on the most expensive steps.

        ``model``/``tier`` file the spend under the rate it is billed at
        (see :attr:`tokens_by`); a call that spent nothing is not counted
        there."""
        self.stats["input_tokens"] += usage.input_tokens
        self.stats["output_tokens"] += usage.output_tokens
        self.stats["thinking_tokens"] += usage.thinking_tokens
        self.stats["cache_read_tokens"] += usage.cache_read_tokens
        self.stats["cache_write_tokens"] += usage.cache_write_tokens
        cache = usage.cache_read_tokens + usage.cache_write_tokens
        if not (usage.input_tokens or cache or usage.output_tokens):
            return
        row = self.tokens_by.setdefault(
            (model, tier),
            {"calls": 0, "input": 0, "cached": 0, "output": 0, "thinking": 0},
        )
        row["calls"] += 1
        row["input"] += usage.input_tokens
        row["cached"] += cache
        row["output"] += usage.output_tokens
        row["thinking"] += usage.thinking_tokens

    def add_step_usage(self, usage: TokenUsage, model: str = "", tier: str = "") -> None:
        """A successful AI call: its usage plus one AI step.

        ai_steps counts committed calls only (matching the report's step
        count); failed calls go through :meth:`add_tokens` alone.
        """
        self.stats["ai_steps"] += 1
        self.add_tokens(usage, model, tier)

    def _section_stats(self) -> dict[str, int]:
        return self.section_stats.setdefault(
            self.current_section, dict.fromkeys(_SECTION_KEYS, 0)
        )

    def add_time(self, ms: int) -> None:
        """Fold the wall-clock time of one ai() step or one single-step AI
        call into the run stats — failed ones included, so the session
        total can be compared against the provider ledger's model time and
        waits, which also see every call."""
        self.stats["step_duration_ms"] += ms
        self._section_stats()["step_duration_ms"] += ms

    def add_ledger(self, window: StatsSnapshot) -> None:
        """Fold a provider-ledger window (model time, capacity waits) into
        the current section. The session totals are read off the ledger
        itself; this only splits them by section."""
        sec = self._section_stats()
        for key in _LEDGER_KEYS:
            sec[key] += getattr(window, key)

    @staticmethod
    def annotate(
        entry: dict[str, Any] | None,
        duration_ms: int,
        window: StatsSnapshot,
        model: str,
        tier: str,
    ) -> None:
        """Attach an AI step's timing, model and tier to its log entry.

        Called once the step is over (action and settle included), so the
        entry recorded before the action gets its full time. Manual steps
        never get these keys, which is how the report tells them apart.
        """
        if entry is None:
            return
        entry["duration_ms"] = duration_ms
        for key in _LEDGER_KEYS:
            entry[key] = getattr(window, key)
        entry["model"] = model
        entry["tier"] = tier

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
