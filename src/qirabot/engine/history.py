"""Sliding-window conversation history.

Mirrors internal/decision/history.go: keeps only the most recent entries to
avoid exceeding context window limits; truncated entries are compressed into
a summary string.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import actions
from .types import ConversationTurn, HistoryState

_MAX_REASONING_LEN = 50


@dataclass
class HistoryConfig:
    """Controls history window and screenshot retention."""

    max_entries: int = 0  # how many text history entries to keep
    # how many of those entries carry screenshots (counted from most recent)
    max_screenshots: int = 0
    # how many raw summary lines to keep; older lines are dropped and only
    # counted. Bounds per-step prompt cost on very long tasks (a summary line
    # is ~15-25 tokens; the default caps the section at a few thousand).
    max_summary_lines: int = 0


def default_history_config() -> HistoryConfig:
    return HistoryConfig(max_entries=5, max_screenshots=1, max_summary_lines=200)


class History:
    """Conversation history with a bounded window and compressed summary."""

    def __init__(self, cfg: HistoryConfig | None = None) -> None:
        cfg = cfg or HistoryConfig()
        default = default_history_config()
        self._max_entries = cfg.max_entries if cfg.max_entries > 0 else default.max_entries
        self._max_screenshots = (
            cfg.max_screenshots if cfg.max_screenshots > 0 else default.max_screenshots
        )
        self._max_summary_lines = (
            cfg.max_summary_lines if cfg.max_summary_lines > 0 else default.max_summary_lines
        )
        self._entries: list[ConversationTurn] = []
        self._summary: list[str] = []
        self._summary_dropped = 0

    @property
    def high_water(self) -> int:
        """Entry count that triggers truncation. Deliberately above
        max_entries: trimming one entry per step would shift the conversation
        prefix every step and defeat provider prompt caching, so entries
        accumulate up to 2x the window and are folded back in one batch —
        the cache prefix then only breaks once per max_entries steps."""
        return 2 * self._max_entries

    def add(self, entry: ConversationTurn) -> None:
        """Append an entry. Once the count exceeds the high-water mark, the
        oldest entries are compressed into the summary in one batch, bringing
        the window back down to max_entries."""
        self._entries.append(entry)
        if len(self._entries) > self.high_water:
            overflow = len(self._entries) - self._max_entries
            for e in self._entries[:overflow]:
                s = _format_summary_entry(e)
                if s:
                    self._summary.append(s)
            self._entries = self._entries[overflow:]
            if len(self._summary) > self._max_summary_lines:
                drop = len(self._summary) - self._max_summary_lines
                self._summary_dropped += drop
                self._summary = self._summary[drop:]

    def backfill_last_tool_output(self, output: str) -> None:
        """Overwrite the most recent entry's tool output — the SDK reports a
        step's execution result with the NEXT request, not at execution time."""
        if self._entries:
            self._entries[-1].tool_output = output

    def attach_screenshots(self, screenshots: list[bytes]) -> None:
        """Inject screenshot data into the most recent max_screenshots entries,
        using reverse alignment with the provided screenshots list.

        Entries live across steps (unlike the v2 server, which rebuilt them
        from Redis per request), so first strip what a previous call attached —
        otherwise every entry in the window keeps its screenshot and each
        request carries max_entries images instead of max_screenshots."""
        for e in self._entries:
            e.screenshot_data = b""
        num = min(len(self._entries), len(screenshots), self._max_screenshots)
        for i in range(num):
            entry_idx = len(self._entries) - num + i
            ss_idx = len(screenshots) - num + i
            self._entries[entry_idx].screenshot_data = screenshots[ss_idx]

    def entries(self) -> list[ConversationTurn]:
        return list(self._entries)

    def summary(self) -> str:
        """Summary of truncated entries, run-length compressed at render time
        (storage keeps the raw lines); empty if none truncated."""
        return _render_summary(self._summary, self._summary_dropped)

    def full_summary(self) -> str:
        """Complete summary combining truncated entries and current window."""
        lines = list(self._summary)
        for e in self._entries:
            s = _format_summary_entry(e)
            if s:
                lines.append(s)
        return _render_summary(lines, self._summary_dropped)

    def export(self) -> HistoryState:
        return HistoryState(
            entries=self.entries(),
            summary=list(self._summary),
            max_entries=self._max_entries,
            max_screenshots=self._max_screenshots,
            max_summary_lines=self._max_summary_lines,
            summary_dropped=self._summary_dropped,
        )

    def clear(self) -> None:
        self._entries.clear()
        self._summary.clear()
        self._summary_dropped = 0


def load_history(state: HistoryState) -> History:
    """Reconstruct a History from a serialized HistoryState."""
    h = History(HistoryConfig(state.max_entries, state.max_screenshots, state.max_summary_lines))
    h._entries = list(state.entries)
    h._summary = list(state.summary)
    h._summary_dropped = state.summary_dropped
    return h


def _action_of(line: str) -> str:
    """The action-type prefix of a summary line ("click: reason" -> "click")."""
    return line.split(": ", 1)[0]


def _compress_lines(lines: list[str]) -> list[str]:
    """Run-length compress consecutive lines sharing an action type:
    ["scroll: a", "scroll: b", "scroll: c"] -> ["scroll ×3: c"] (the LAST
    reason wins — it reflects the most recent intent). Deterministic, so the
    rendered summary is byte-stable between truncation events and safe for
    the provider cache (it lives in the tail of the contents anyway).
    Collapsing repeats also makes loops MORE visible to the model ("×13"
    reads louder than 13 near-identical lines)."""
    out: list[str] = []
    i = 0
    while i < len(lines):
        action = _action_of(lines[i])
        j = i
        while j + 1 < len(lines) and _action_of(lines[j + 1]) == action:
            j += 1
        if j == i:
            out.append(lines[i])
        else:
            last = lines[j]
            reason = last.split(": ", 1)[1] if ": " in last else ""
            out.append(f"{action} ×{j - i + 1}" + (f": {reason}" if reason else ""))
        i = j + 1
    return out


def _render_summary(lines: list[str], dropped: int) -> str:
    parts: list[str] = []
    if dropped:
        parts.append(f"(earliest {dropped} actions omitted)")
    parts.extend(_compress_lines(lines))
    return "\n".join(parts)


def _format_summary_entry(e: ConversationTurn) -> str:
    """Format a single history entry for the summary. save_note entries only
    show nothing at all, to avoid duplicating note content."""
    if e.action_type == actions.SAVE_NOTE:
        return ""
    reasoning = e.reasoning
    if len(reasoning) > _MAX_REASONING_LEN:
        reasoning = reasoning[:_MAX_REASONING_LEN] + "..."
    if reasoning:
        return f"{e.action_type}: {reasoning}"
    return e.action_type
