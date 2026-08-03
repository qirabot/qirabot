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


def default_history_config() -> HistoryConfig:
    return HistoryConfig(max_entries=5, max_screenshots=1)


class History:
    """Conversation history with a bounded window and compressed summary."""

    def __init__(self, cfg: HistoryConfig | None = None) -> None:
        cfg = cfg or HistoryConfig()
        default = default_history_config()
        self._max_entries = cfg.max_entries if cfg.max_entries > 0 else default.max_entries
        self._max_screenshots = (
            cfg.max_screenshots if cfg.max_screenshots > 0 else default.max_screenshots
        )
        self._entries: list[ConversationTurn] = []
        self._summary: list[str] = []

    def add(self, entry: ConversationTurn) -> None:
        """Append an entry, trimming old entries if needed. Truncated entries
        are compressed into the summary."""
        self._entries.append(entry)
        if len(self._entries) > self._max_entries:
            overflow = len(self._entries) - self._max_entries
            for e in self._entries[:overflow]:
                s = _format_summary_entry(e)
                if s:
                    self._summary.append(s)
            self._entries = self._entries[overflow:]

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
        """Compressed summary of truncated entries; empty if none truncated."""
        return "\n".join(self._summary)

    def full_summary(self) -> str:
        """Complete summary combining truncated entries and current window."""
        parts = list(self._summary)
        for e in self._entries:
            s = _format_summary_entry(e)
            if s:
                parts.append(s)
        return "\n".join(parts)

    def export(self) -> HistoryState:
        return HistoryState(
            entries=self.entries(),
            summary=list(self._summary),
            max_entries=self._max_entries,
            max_screenshots=self._max_screenshots,
        )

    def clear(self) -> None:
        self._entries.clear()
        self._summary.clear()


def load_history(state: HistoryState) -> History:
    """Reconstruct a History from a serialized HistoryState."""
    h = History(HistoryConfig(state.max_entries, state.max_screenshots))
    h._entries = list(state.entries)
    h._summary = list(state.summary)
    return h


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
