"""Unit tests for the bot.ai() knowledge= resolver (``qirabot._knowledge``)
plus the wire/echo behavior in the ai() loop.

Everything is client-side and offline: the resolver turns str/Path/list input
into the plain text sent to the engine, and every failure mode must raise
``ValueError`` before any request goes out. The loop tests reuse the scripted
harness from test_custom_tools — knowledge shares the same first-request
registration and missing-echo-detection contract as tools. Engine-side echo
behavior lives in tests/engine/test_local_backend.py, not here.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from qirabot._knowledge import MAX_KNOWLEDGE_BYTES, resolve_knowledge

from tests.test_custom_tools import DONE, _ToolLoopHarness


def test_str_passes_through_verbatim():
    assert resolve_knowledge("GM 只能使用一次") == "GM 只能使用一次"


def test_str_is_never_interpreted_as_path(tmp_path: Path):
    # A plain string that happens to name an existing file must stay literal
    # text — only pathlib.Path opts into file reading.
    f = tmp_path / "rules.md"
    f.write_text("file content", encoding="utf-8")
    assert resolve_knowledge(str(f)) == str(f)


def test_path_reads_file(tmp_path: Path):
    f = tmp_path / "rules.md"
    f.write_text("每日副本上限 3 次\n", encoding="utf-8")
    assert resolve_knowledge(f) == "每日副本上限 3 次"


def test_path_missing_file_raises(tmp_path: Path):
    with pytest.raises(ValueError, match="cannot read"):
        resolve_knowledge(tmp_path / "nope.md")


def test_path_non_utf8_raises(tmp_path: Path):
    f = tmp_path / "rules.bin"
    f.write_bytes(b"\xff\xfe\x00rules")
    with pytest.raises(ValueError, match="not UTF-8"):
        resolve_knowledge(f)


def test_list_mixes_text_and_files(tmp_path: Path):
    f = tmp_path / "common.md"
    f.write_text("通用规则", encoding="utf-8")
    assert resolve_knowledge([f, "本关 boss 有二阶段"]) == "通用规则\n\n本关 boss 有二阶段"


def test_list_skips_blank_entries():
    assert resolve_knowledge(["", "  ", "有效知识"]) == "有效知识"


def test_empty_input_resolves_to_empty_string():
    # The SDK omits the wire field entirely for "" — no knowledge registered.
    assert resolve_knowledge("") == ""
    assert resolve_knowledge([]) == ""


def test_unsupported_entry_type_raises():
    with pytest.raises(ValueError, match="str or pathlib.Path"):
        resolve_knowledge([{"not": "supported"}])  # type: ignore[list-item]


def test_over_limit_raises_with_per_source_breakdown(tmp_path: Path):
    f = tmp_path / "big.md"
    f.write_text("x" * MAX_KNOWLEDGE_BYTES, encoding="utf-8")
    with pytest.raises(ValueError) as exc:
        resolve_knowledge([f, "small extra"])
    assert "exceeds" in str(exc.value)
    # The breakdown names each source so the caller knows what to trim.
    assert "big.md" in str(exc.value)
    assert "text #2" in str(exc.value)


def test_limit_counts_utf8_bytes_not_chars():
    # 3 bytes per CJK char: fits by chars, overflows by bytes.
    text = "知" * (MAX_KNOWLEDGE_BYTES // 3 + 1)
    with pytest.raises(ValueError, match="exceeds"):
        resolve_knowledge(text)


class TestAiLoopKnowledge:
    def test_first_request_carries_knowledge(self):
        done = dict(DONE)
        done["knowledge_registered"] = 15
        h = _ToolLoopHarness([done])
        h.bot.ai(object(), "do it", max_steps=3, knowledge="GM 只能使用一次")
        h.bot.close()

        assert h.bodies[0]["action"]["params"]["knowledge"] == "GM 只能使用一次"

    def test_no_knowledge_means_no_key(self):
        h = _ToolLoopHarness([DONE])
        h.bot.ai(object(), "do it", max_steps=3)
        h.bot.close()

        assert "knowledge" not in h.bodies[0]["action"]["params"]

    def test_old_server_warning(self, caplog):
        # A successful step-1 response without the knowledge_registered echo
        # means the backend ignored the knowledge param.
        h = _ToolLoopHarness([DONE])
        with caplog.at_level(logging.WARNING, logger="qirabot"):
            h.bot.ai(object(), "do it", max_steps=3, knowledge="规则文本")
        h.bot.close()
        assert any("does not support knowledge" in r.getMessage() for r in caplog.records)

    def test_no_false_warning_when_echoed(self, caplog):
        done = dict(DONE)
        done["knowledge_registered"] = 12
        h = _ToolLoopHarness([done])
        with caplog.at_level(logging.INFO, logger="qirabot"):
            h.bot.ai(object(), "do it", max_steps=3, knowledge="规则文本")
        h.bot.close()
        assert not any("does not support" in r.getMessage() for r in caplog.records)
        assert any("knowledge registered: 12 bytes" in r.getMessage() for r in caplog.records)
