"""Tests for the HTML report renderer, focused on outcome badges."""

from qirabot.report import write_html


def _entry(section, **over):
    e = {
        "section": section,
        "action_type": "click",
        "params": {"locate": "button"},
        "decision": "",
        "output": "",
        "finished": False,
        "success": True,
        "coords": None,
        "screenshot": "",
        "thumb": "",
    }
    e.update(over)
    return e


class TestSectionBadges:
    def test_four_statuses_render_distinct_badges(self, tmp_path):
        log = [_entry(s) for s in ("a", "b", "c", "d")]
        out = write_html(
            log,
            tmp_path / "report.html",
            outcomes={
                "a": "completed",
                "b": "goal_failed",
                "c": "max_steps",
                "d": "error",
            },
        )
        html = out.read_text(encoding="utf-8")
        # Each judged task renders as its own summary grid row: an aligned
        # status badge first, then the name (CSS-truncated, full text in the
        # hover title — long ai() instructions would otherwise flood the
        # summary).
        def row(kind, name, label):
            return (
                f'<span class="badge {kind}">{label}</span>'
                f'<span class="tname" title="{name}">{name}</span>'
            )

        assert row("pass", "a", "PASS") in html
        assert row("fail", "b", "FAIL") in html
        assert row("warn", "c", "MAX STEPS") in html
        assert row("fail", "d", "ERROR") in html

    def test_legacy_bool_outcomes_still_render(self, tmp_path):
        # Pre-status callers passed dict[str, bool]; True/False must keep
        # rendering as PASS/FAIL.
        log = [_entry("a"), _entry("b")]
        out = write_html(
            log, tmp_path / "report.html", outcomes={"a": True, "b": False}
        )
        html = out.read_text(encoding="utf-8")
        assert '<span class="badge pass">PASS</span><span class="tname" title="a">' in html
        assert '<span class="badge fail">FAIL</span><span class="tname" title="b">' in html

    def test_unknown_status_falls_back_to_fail(self, tmp_path):
        out = write_html(
            [_entry("a")], tmp_path / "report.html", outcomes={"a": "someday-new"}
        )
        assert '<span class="badge fail">FAIL</span><span class="tname" title="a">' in (
            out.read_text(encoding="utf-8")
        )

    def test_unjudged_section_stays_neutral(self, tmp_path):
        out = write_html([_entry("setup")], tmp_path / "report.html", outcomes={})
        assert "badge neutral" in out.read_text(encoding="utf-8")


class TestTimelineGrouping:
    def test_interleaved_manual_steps_stay_in_execution_order(self, tmp_path):
        # manual → ai(a) → manual → ai(b) must render as four sections in
        # that order, not one merged manual block up front.
        log = [_entry("setup"), _entry("a"), _entry("setup"), _entry("b")]
        out = write_html(
            log,
            tmp_path / "report.html",
            outcomes={"a": "completed", "b": "completed"},
        )
        html = out.read_text(encoding="utf-8")
        first_manual = html.index("<h2>manual")
        sec_a = html.index("<h2>a ")
        second_manual = html.index("<h2>manual", first_manual + 1)
        sec_b = html.index("<h2>b ")
        assert first_manual < sec_a < second_manual < sec_b

    def test_setup_section_displays_as_manual(self, tmp_path):
        # The log key stays "setup" (client outcomes/errors are keyed on it),
        # but the report renders standalone actions as "manual".
        out = write_html([_entry("setup")], tmp_path / "report.html")
        html = out.read_text(encoding="utf-8")
        assert "<h2>manual" in html
        assert "<h2>setup" not in html

    def test_summary_counts_split_section_once(self, tmp_path):
        # A judged section whose steps split across two timeline groups is
        # still one section in the pass tally.
        log = [_entry("a"), _entry("setup"), _entry("a")]
        out = write_html(
            log, tmp_path / "report.html", outcomes={"a": "completed"}
        )
        assert "1/1 passed" in out.read_text(encoding="utf-8")

    def test_error_banner_renders_only_on_last_group(self, tmp_path):
        # A section that ran twice and errored on the second run shows the
        # banner once, under its final group.
        log = [_entry("a"), _entry("setup"), _entry("a")]
        out = write_html(
            log,
            tmp_path / "report.html",
            outcomes={"a": "error"},
            section_errors={"a": "session expired"},
        )
        html = out.read_text(encoding="utf-8")
        assert html.count("✗ session expired") == 1
        # The banner sits after the second "a" heading, not the first.
        assert html.index("✗ session expired") > html.rindex("<h2>a ")


class TestSummaryBadge:
    def test_all_completed_is_green(self, tmp_path):
        out = write_html(
            [_entry("a")], tmp_path / "report.html", outcomes={"a": "completed"}
        )
        assert '<span class="badge pass">1/1 passed</span>' in out.read_text(
            encoding="utf-8"
        )

    def test_only_truncation_is_amber(self, tmp_path):
        log = [_entry("a"), _entry("b")]
        out = write_html(
            log,
            tmp_path / "report.html",
            outcomes={"a": "completed", "b": "max_steps"},
        )
        assert (
            '<span class="badge warn">1/2 passed · 1 truncated</span>'
            in out.read_text(encoding="utf-8")
        )

    def test_tally_renders_on_its_own_line(self, tmp_path):
        # The tally is the report's headline: it gets its own .tally block
        # above the per-task summary instead of flowing inline with it.
        out = write_html(
            [_entry("a")], tmp_path / "report.html", outcomes={"a": "completed"}
        )
        html = out.read_text(encoding="utf-8")
        assert (
            "<div class='tally'>"
            '<span class="badge pass">1/1 passed</span></div>' in html
        )

    def test_manual_groups_stay_out_of_summary(self, tmp_path):
        # Manual step counts are process detail (stats line + section
        # headers), not outcomes — they no longer get summary chips.
        log = [_entry("setup"), _entry("a"), _entry("setup")]
        out = write_html(
            log, tmp_path / "report.html", outcomes={"a": "completed"}
        )
        html = out.read_text(encoding="utf-8")
        assert "manual: 1 steps" not in html
        assert '<span class="tname" title="a">a</span>' in html

    def test_any_real_failure_is_red(self, tmp_path):
        log = [_entry("a"), _entry("b"), _entry("c")]
        out = write_html(
            log,
            tmp_path / "report.html",
            outcomes={"a": "completed", "b": "max_steps", "c": "goal_failed"},
        )
        assert (
            '<span class="badge fail">1/3 passed · 1 truncated</span>'
            in out.read_text(encoding="utf-8")
        )


class TestWarnRow:
    def test_warn_entry_renders_amber_not_red(self, tmp_path):
        log = [
            _entry(
                "a",
                action_type="ai",
                output="max steps reached",
                finished=True,
                success=False,
                warn=True,
            )
        ]
        out = write_html(log, tmp_path / "report.html", outcomes={"a": "max_steps"})
        html = out.read_text(encoding="utf-8")
        # Row classes appear in the body as class='act warn-row' etc.; the
        # stylesheet always mentions fail-row, so match the attribute form.
        assert "'act warn-row'" in html
        assert "'act fail-row'" not in html
        assert "ai ⚠" in html

    def test_failed_entry_still_renders_red(self, tmp_path):
        log = [_entry("a", success=False, output="boom")]
        out = write_html(log, tmp_path / "report.html", outcomes={"a": "error"})
        html = out.read_text(encoding="utf-8")
        assert "'act fail-row'" in html
        assert "✗" in html


class TestReusedFrameLabel:
    """Two steps sharing one image must say so — otherwise the repeat reads as
    a fresh capture of an unchanged screen."""

    def test_reused_entry_is_labelled(self, tmp_path):
        log = [
            _entry("a", thumb="data:image/jpeg;base64,x", screenshot="screenshots/1.jpg"),
            _entry(
                "a",
                action_type="scroll",
                thumb="data:image/jpeg;base64,x",
                screenshot="screenshots/1.jpg",
                reused_frame=True,
            ),
        ]
        out = write_html(log, tmp_path / "report.html")
        html = out.read_text(encoding="utf-8")
        # Once in the step cell, once in the lightbox label for that shot.
        assert html.count("frame from an earlier step") == 2

    def test_fresh_entries_carry_no_label(self, tmp_path):
        log = [_entry("a", thumb="data:image/jpeg;base64,x")]
        out = write_html(log, tmp_path / "report.html")
        assert "frame from an earlier step" not in out.read_text(encoding="utf-8")


class TestStepTimestamps:
    def test_offsets_relative_to_first_step_without_recording(self, tmp_path):
        log = [_entry("a", ts=1000.0), _entry("a", ts=1092.0)]
        out = write_html(log, tmp_path / "report.html")
        html = out.read_text(encoding="utf-8")
        assert "+0:00" in html
        assert "+1:32" in html
        # No video on the page → offsets are plain text, not seek links.
        assert "seekTo" not in html

    def test_offsets_seek_video_when_recording_start_known(self, tmp_path):
        log = [_entry("a", ts=1005.5)]
        out = write_html(
            log,
            tmp_path / "report.html",
            recording="recording.mp4",
            recording_start=1000.0,
        )
        html = out.read_text(encoding="utf-8")
        assert "seekTo(5.5)" in html
        assert "+0:05" in html

    def test_recording_without_start_ts_stays_unlinked(self, tmp_path):
        # Recording present but its start time unknown (e.g. an external
        # recording.mp4 dropped into report_dir): offsets fall back to
        # run-relative plain text; only the video player's own script exists.
        log = [_entry("a", ts=1000.0), _entry("a", ts=1007.0)]
        out = write_html(log, tmp_path / "report.html", recording="recording.mp4")
        html = out.read_text(encoding="utf-8")
        assert "+0:07" in html
        assert "onclick='return seekTo" not in html

    def test_entries_without_ts_render_blank_time_cell(self, tmp_path):
        # Logs from before the ts field (or external write_html callers) must
        # still render — time cells just stay empty.
        out = write_html([_entry("a")], tmp_path / "report.html")
        html = out.read_text(encoding="utf-8")
        assert ">time<" in html
        assert "seekTo" not in html


class TestSectionErrorBanners:
    def test_max_steps_banner_renders_amber(self, tmp_path):
        out = write_html(
            [_entry("a")],
            tmp_path / "report.html",
            outcomes={"a": "max_steps"},
            section_errors={"a": "max steps reached (5)"},
        )
        html = out.read_text(encoding="utf-8")
        assert "max steps reached (5)" in html
        assert "<div class='notice'>⚠ max steps reached (5)</div>" in html

    def test_error_banner_renders_red(self, tmp_path):
        out = write_html(
            [_entry("a")],
            tmp_path / "report.html",
            outcomes={"a": "error"},
            section_errors={"a": "session expired"},
        )
        html = out.read_text(encoding="utf-8")
        assert "<div class='notice error'>✗ session expired</div>" in html

    def test_error_only_section_still_renders(self, tmp_path):
        # A section that failed before recording any step must still show its
        # banner (and badge) — otherwise the failure reason vanishes.
        out = write_html(
            [_entry("a")],
            tmp_path / "report.html",
            outcomes={"a": "completed", "b": "error"},
            section_errors={"b": "AI request failed"},
        )
        html = out.read_text(encoding="utf-8")
        assert "AI request failed" in html
        # summary line for the error-only section
        assert '<span class="badge fail">ERROR</span><span class="tname" title="b">' in html


class TestRunInfo:
    def test_total_steps_headline_with_ai_subset(self, tmp_path):
        out = write_html(
            [_entry("a"), _entry("a"), _entry("a")],
            tmp_path / "report.html",
            stats={"total_steps": 3, "ai_steps": 2, "input_tokens": 1000,
                   "output_tokens": 500, "thinking_tokens": 0,
                   "step_duration_ms": 1200},
        )
        html = out.read_text(encoding="utf-8")
        assert "<dt>Steps</dt><dd>3 (2 AI-decided)</dd>" in html

    def test_pure_local_run_still_renders_stats(self, tmp_path):
        # 0 AI steps used to suppress the stats line entirely; a purely local
        # script still deserves its step count.
        out = write_html(
            [_entry("setup"), _entry("setup")],
            tmp_path / "report.html",
            stats={"total_steps": 2, "ai_steps": 0},
        )
        html = out.read_text(encoding="utf-8")
        assert "<dt>Steps</dt><dd>2 (0 AI-decided)</dd>" in html

    def test_every_value_carries_a_label(self, tmp_path):
        out = write_html(
            [_entry("a")],
            tmp_path / "report.html",
            stats={"total_steps": 1, "ai_steps": 1, "input_tokens": 100,
                   "output_tokens": 50},
            model="gemini-vertex/gemini-3.6-flash",
            tier="priority (served: standard)",
        )
        html = out.read_text(encoding="utf-8")
        # A reader should never have to work out what a bare string is.
        assert "<dt>Model</dt><dd>gemini-vertex/gemini-3.6-flash</dd>" in html
        assert "<dt>Tier</dt><dd>priority (served: standard)</dd>" in html
        assert "<dt>Tokens</dt><dd>150 total — 100 prompt, 50 response</dd>" in html

    def test_time_names_where_it_went(self, tmp_path):
        out = write_html(
            [_entry("a")],
            tmp_path / "report.html",
            stats={"total_steps": 1, "ai_steps": 1,
                   "step_duration_ms": 10_000, "llm_decision_duration_ms": 4_000},
        )
        html = out.read_text(encoding="utf-8")
        assert "<dt>Time</dt><dd>10.0s in steps · 4.0s waiting on the model</dd>" in html

    def test_an_all_model_run_says_so_in_words(self, tmp_path):
        # Printing the same number twice reads like a bug.
        out = write_html(
            [_entry("a")],
            tmp_path / "report.html",
            stats={"total_steps": 1, "ai_steps": 1,
                   "step_duration_ms": 10_000, "llm_decision_duration_ms": 9_900},
        )
        html = out.read_text(encoding="utf-8")
        assert "almost all of it waiting on the model" in html
        assert "9.9s" not in html

    def test_an_unconfigured_tier_gets_no_row(self, tmp_path):
        out = write_html(
            [_entry("a")],
            tmp_path / "report.html",
            stats={"total_steps": 1, "ai_steps": 1},
            model="gemini-vertex/gemini-3.6-flash",
        )
        assert "<dt>Tier</dt>" not in out.read_text(encoding="utf-8")

    def test_legacy_stats_without_total_steps_fall_back_to_log_length(self, tmp_path):
        out = write_html(
            [_entry("a")],
            tmp_path / "report.html",
            stats={"ai_steps": 1},
        )
        html = out.read_text(encoding="utf-8")
        assert "<dt>Steps</dt><dd>1 (all AI-decided)</dd>" in html


class TestStatusVocabulary:
    def test_badge_map_covers_run_status_exactly(self):
        # qirabot.client.RunStatus is the single status vocabulary; the
        # report's badge map must cover it exactly — a new status added there
        # without a badge here would render as a misleading FAIL fallback.
        from typing import get_args

        from qirabot.client import RunStatus
        from qirabot.report import _STATUS_KINDS

        assert set(_STATUS_KINDS) == set(get_args(RunStatus))
