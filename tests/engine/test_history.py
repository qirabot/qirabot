"""History window, summary compression and export/load — ported from the Go
engine's history.go semantics (partially covered there via export_test.go)."""

from qirabot.engine.history import History, HistoryConfig, default_history_config, load_history
from qirabot.engine.types import ConversationTurn, HistoryState


def turn(action: str, reasoning: str = "", tool_output: str = "") -> ConversationTurn:
    return ConversationTurn(action_type=action, reasoning=reasoning, tool_output=tool_output)


class TestWindow:
    def test_defaults_applied_for_zero_config(self) -> None:
        h = History(HistoryConfig())
        for i in range(10):
            h.add(turn(f"a{i}"))
        assert len(h.entries()) == default_history_config().max_entries

    def test_overflow_compressed_into_summary(self) -> None:
        h = History(HistoryConfig(max_entries=2, max_screenshots=1))
        h.add(turn("click", "打开设置"))
        h.add(turn("scroll", "向下找入口"))
        h.add(turn("wait"))
        entries = h.entries()
        assert [e.action_type for e in entries] == ["scroll", "wait"]
        assert h.summary() == "click: 打开设置"

    def test_summary_reasoning_truncated_at_50_chars(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        long_reason = "长" * 60
        h.add(turn("click", long_reason))
        h.add(turn("wait"))
        assert h.summary() == "click: " + "长" * 50 + "..."

    def test_save_note_dropped_from_summary(self) -> None:
        # save_note entries only pollute the summary with duplicated note
        # content, so compression drops them entirely.
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        h.add(turn("save_note", "记录了页面内容"))
        h.add(turn("click", "下一页"))
        h.add(turn("wait"))
        assert h.summary() == "click: 下一页"

    def test_full_summary_includes_window(self) -> None:
        h = History(HistoryConfig(max_entries=2, max_screenshots=1))
        h.add(turn("click", "第一步"))
        h.add(turn("scroll", "第二步"))
        h.add(turn("wait"))
        assert h.full_summary() == "click: 第一步\nscroll: 第二步\nwait"

    def test_entry_without_reasoning_uses_bare_action_type(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        h.add(turn("click"))
        h.add(turn("wait"))
        assert h.summary() == "click"


class TestScreenshots:
    def test_attach_reverse_alignment(self) -> None:
        h = History(HistoryConfig(max_entries=5, max_screenshots=2))
        for i in range(3):
            h.add(turn(f"a{i}"))
        h.attach_screenshots([b"s0", b"s1", b"s2"])
        entries = h.entries()
        # Only the most recent max_screenshots entries carry screenshots,
        # aligned from the tail of both lists.
        assert entries[0].screenshot_data == b""
        assert entries[1].screenshot_data == b"s1"
        assert entries[2].screenshot_data == b"s2"

    def test_reattach_strips_previous_step_screenshots(self) -> None:
        # The session calls attach_screenshots once per step on a History
        # whose entries persist across steps. A re-attach must clear what the
        # previous call set, or every entry in the window accumulates a
        # screenshot and each request carries max_entries images instead of
        # max_screenshots (observed as ~1120 input tokens of growth per step).
        h = History(HistoryConfig(max_entries=5, max_screenshots=1))
        shots: list[bytes] = []
        for i in range(4):
            h.add(turn(f"a{i}"))
            shots.append(f"s{i}".encode())
            h.attach_screenshots(shots)
        entries = h.entries()
        assert [e.screenshot_data for e in entries] == [b"", b"", b"", b"s3"]

    def test_attach_fewer_screenshots_than_entries(self) -> None:
        h = History(HistoryConfig(max_entries=5, max_screenshots=3))
        for i in range(3):
            h.add(turn(f"a{i}"))
        h.attach_screenshots([b"only"])
        entries = h.entries()
        assert entries[2].screenshot_data == b"only"
        assert entries[0].screenshot_data == b""


class TestExportLoad:
    def test_roundtrip(self) -> None:
        h = History(HistoryConfig(max_entries=2, max_screenshots=1))
        h.add(turn("click", "第一步"))
        h.add(turn("scroll", "第二步"))
        h.add(turn("wait"))

        state = h.export()
        assert state.max_entries == 2
        assert state.max_screenshots == 1
        assert state.summary == ["click: 第一步"]

        restored = load_history(state)
        assert [e.action_type for e in restored.entries()] == ["scroll", "wait"]
        assert restored.summary() == "click: 第一步"
        # The restored window keeps enforcing the same bounds.
        restored.add(turn("done"))
        assert len(restored.entries()) == 2

    def test_load_zero_config_falls_back_to_defaults(self) -> None:
        restored = load_history(HistoryState())
        for i in range(10):
            restored.add(turn(f"a{i}"))
        assert len(restored.entries()) == default_history_config().max_entries

    def test_clear(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        h.add(turn("click"))
        h.add(turn("wait"))
        h.clear()
        assert h.entries() == []
        assert h.summary() == ""
