"""History window, summary compression and export/load."""

from qirabot.engine.history import History, HistoryConfig, default_history_config, load_history
from qirabot.engine.types import ConversationTurn, HistoryState


def turn(action: str, reasoning: str = "", tool_output: str = "") -> ConversationTurn:
    return ConversationTurn(action_type=action, reasoning=reasoning, tool_output=tool_output)


class TestWindow:
    def test_defaults_applied_for_zero_config(self) -> None:
        h = History(HistoryConfig())
        for i in range(2 * default_history_config().max_entries + 1):
            h.add(turn(f"a{i}"))
        assert len(h.entries()) == default_history_config().max_entries

    def test_no_trim_until_high_water(self) -> None:
        # Trimming one entry per step would shift the conversation prefix
        # every step and defeat provider prompt caching, so entries
        # accumulate up to 2x the window before any truncation.
        h = History(HistoryConfig(max_entries=2, max_screenshots=1))
        assert h.high_water == 4
        for i in range(4):
            h.add(turn(f"a{i}"))
        assert len(h.entries()) == 4
        assert h.summary() == ""

    def test_batch_trim_folds_back_to_max_entries(self) -> None:
        h = History(HistoryConfig(max_entries=2, max_screenshots=1))
        h.add(turn("click", "打开设置"))
        h.add(turn("scroll", "向下找入口"))
        h.add(turn("wait"))
        h.add(turn("click", "确认"))
        h.add(turn("done"))  # 5 > high_water 4: fold the oldest 3 in one batch
        assert [e.action_type for e in h.entries()] == ["click", "done"]
        assert h.summary() == "click: 打开设置\nscroll: 向下找入口\nwait"

    def test_summary_reasoning_truncated_at_50_chars(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        long_reason = "长" * 60
        h.add(turn("click", long_reason))
        h.add(turn("wait"))
        h.add(turn("done"))  # 3 > high_water 2: fold click + wait
        assert h.summary() == "click: " + "长" * 50 + "...\nwait"

    def test_save_note_dropped_from_summary(self) -> None:
        # save_note entries only pollute the summary with duplicated note
        # content, so compression drops them entirely.
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        h.add(turn("save_note", "记录了页面内容"))
        h.add(turn("click", "下一页"))
        h.add(turn("wait"))  # 3 > high_water 2: fold save_note + click
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
        h.add(turn("done"))
        assert h.summary() == "click\nwait"


class TestSummaryCompression:
    def test_consecutive_same_action_merged_with_last_reason(self) -> None:
        # 13 near-identical "scroll: ..." lines carry less signal than one
        # "scroll ×13" — merging saves tokens AND makes loops more visible.
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        h.add(turn("scroll", "向下找 A"))
        h.add(turn("scroll", "向下找 B"))
        h.add(turn("scroll", "向下找 C"))  # folds A, B
        h.add(turn("click", "点开"))
        h.add(turn("wait"))  # folds C, click
        # Raw storage keeps individual lines; compression is render-only.
        assert h.export().summary == [
            "scroll: 向下找 A",
            "scroll: 向下找 B",
            "scroll: 向下找 C",
            "click: 点开",
        ]
        # The LAST reason wins — it reflects the most recent intent.
        assert h.summary() == "scroll ×3: 向下找 C\nclick: 点开"
        # Deterministic: byte-identical across renders (cache safety).
        assert h.summary() == h.summary()

    def test_bare_action_lines_merged_without_reason(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        h.add(turn("wait"))
        h.add(turn("wait"))
        h.add(turn("done"))  # folds the two waits
        assert h.summary() == "wait ×2"

    def test_distinct_actions_not_merged(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        h.add(turn("click", "第一步"))
        h.add(turn("scroll", "第二步"))
        h.add(turn("wait"))  # folds click, scroll
        assert h.summary() == "click: 第一步\nscroll: 第二步"


class TestSummaryCap:
    def test_oldest_lines_dropped_beyond_cap(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1, max_summary_lines=2))
        for i in range(5):
            h.add(turn(f"a{i}"))
        # Folds happened at adds 3 (a0, a1) and 5 (a2, a3); cap 2 dropped the
        # two oldest raw lines and kept only a count of them.
        assert h.summary() == "(earliest 2 actions omitted)\na2\na3"
        assert h.export().summary_dropped == 2

    def test_dropped_counter_survives_roundtrip(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1, max_summary_lines=2))
        for i in range(5):
            h.add(turn(f"a{i}"))
        restored = load_history(h.export())
        assert restored.summary() == "(earliest 2 actions omitted)\na2\na3"
        # The restored history keeps enforcing the cap.
        restored.add(turn("b0"))
        restored.add(turn("b1"))  # folds a4, b0 -> 4 raw lines -> drop 2 more
        assert restored.export().summary_dropped == 4
        assert restored.summary() == "(earliest 4 actions omitted)\na4\nb0"


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
        h.add(turn("click", "第四步"))
        h.add(turn("done"))  # 5 > high_water 4: fold the oldest 3

        state = h.export()
        assert state.max_entries == 2
        assert state.max_screenshots == 1
        assert state.summary == ["click: 第一步", "scroll: 第二步", "wait"]

        restored = load_history(state)
        assert [e.action_type for e in restored.entries()] == ["click", "done"]
        assert restored.summary() == "click: 第一步\nscroll: 第二步\nwait"
        # The restored window keeps enforcing the same bounds.
        for i in range(3):
            restored.add(turn(f"a{i}"))  # reaches 5 > high_water 4 again
        assert len(restored.entries()) == 2

    def test_load_zero_config_falls_back_to_defaults(self) -> None:
        restored = load_history(HistoryState())
        for i in range(2 * default_history_config().max_entries + 1):
            restored.add(turn(f"a{i}"))
        assert len(restored.entries()) == default_history_config().max_entries

    def test_clear(self) -> None:
        h = History(HistoryConfig(max_entries=1, max_screenshots=1))
        h.add(turn("click"))
        h.add(turn("wait"))
        h.clear()
        assert h.entries() == []
        assert h.summary() == ""
