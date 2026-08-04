"""note_subsumed_by — the storage-side guard against models re-saving the
whole accumulated list on every save_note (prompts forbid it; this makes the
notes store linear even when a model ignores them). The JSON cases replay a
real 2026-08-04 bilibili run where a plain substring check would have missed
both re-saves (brackets, then compact -> pretty-printed reformatting)."""

from qirabot.engine.session import note_subsumed_by

# Real run data (run 023040-local-4d), lightly truncated: compact 1-item,
# compact 2-item superset, pretty-printed 3-item superset.
NOTE1 = '[{"title": "小时候看不懂，长大后已是局中人", "up": "就叫阿路8", "play_count": "586.2万"}]'
NOTE2 = (
    '[{"title": "小时候看不懂，长大后已是局中人", "up": "就叫阿路8", "play_count": "586.2万"}, '
    '{"title": "几十块入手世界级顶尖好物——居家用品篇", "up": "环球百强", "play_count": "267.6万"}]'
)
NOTE3 = (
    "[\n"
    '  {"title": "小时候看不懂，长大后已是局中人", "up": "就叫阿路8", "play_count": "586.2万"},\n'
    '  {"title": "几十块入手世界级顶尖好物——居家用品篇", "up": "环球百强", "play_count": "267.6万"},\n'
    '  {"title": "大家还想看我搬空什么店", "up": "李炮炮儿", "play_count": "312.5万"}\n'
    "]"
)


class TestJsonArrays:
    def test_cumulative_resave_compact(self) -> None:
        # "[{A}]" is not a substring of "[{A}, {B}]" — the JSON path must
        # catch what a substring check can't.
        assert note_subsumed_by(NOTE1, NOTE2)

    def test_cumulative_resave_reformatted(self) -> None:
        # compact -> pretty-printed: same elements, different bytes.
        assert note_subsumed_by(NOTE2, NOTE3)
        assert note_subsumed_by(NOTE1, NOTE3)

    def test_key_order_irrelevant(self) -> None:
        assert note_subsumed_by('[{"a": 1, "b": 2}]', '[{"b": 2, "a": 1}, {"c": 3}]')

    def test_partial_overlap_keeps_both(self) -> None:
        # New note misses an old element: dropping the old note would lose
        # data, so it must NOT be judged subsumed.
        assert not note_subsumed_by(NOTE2, '[{"title": "大家还想看我搬空什么店"}]')
        assert not note_subsumed_by(NOTE3, NOTE2)  # shrinking is not subsumption

    def test_exact_duplicate(self) -> None:
        assert note_subsumed_by(NOTE1, NOTE1)

    def test_empty_array_subsumed_by_any_array(self) -> None:
        assert note_subsumed_by("[]", NOTE1)


class TestPlainText:
    def test_whitespace_normalized_containment(self) -> None:
        assert note_subsumed_by("第一页:标题A", "第一页: 标题A\n第二页: 标题B")

    def test_unrelated_text_kept(self) -> None:
        assert not note_subsumed_by("第一页:标题A", "第二页:标题B")

    def test_json_old_inside_plain_text_new(self) -> None:
        # Old parses as JSON but new doesn't: falls back to the substring
        # check, which still detects verbatim inclusion.
        assert note_subsumed_by(NOTE1, "补充说明\n" + NOTE1)

    def test_json_objects_use_text_fallback(self) -> None:
        # Top-level dicts (not arrays) take the substring path.
        assert note_subsumed_by('{"a": 1}', 'prefix {"a": 1} suffix')
        assert not note_subsumed_by('{"a": 1}', '{"a": 1, "b": 2}')
