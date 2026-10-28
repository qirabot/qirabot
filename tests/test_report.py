"""Tests for the HTML report renderer."""

import time

from qirabot.report import _render_markdown, _summarize_params, write_html


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


def _ai_entry(section, **over):
    """An AI step as the client annotates it once the step is over."""
    e = _entry(
        section,
        duration_ms=4_000,
        model_ms=3_000,
        rate_limit_wait_ms=0,
        outage_wait_ms=0,
        timeout_wait_ms=0,
        model="gemini-3.5-flash",
        tier="standard",
    )
    e.update(over)
    return e


def _render(tmp_path, log, **kwargs):
    return write_html(log, tmp_path / "report.html", **kwargs).read_text(encoding="utf-8")


def _rows(html):
    """The step-table body only: CSS and scripts mention every class."""
    start = html.index("<table class='steps'>")
    return html[start:html.rindex("</table>")]


class TestParamSummary:
    def test_screen_coordinates_compact(self):
        assert _summarize_params({"locate": "OK", "x": 10, "y": 20.6}) == "OK (10, 20)"
        drag = {"x": 1, "y": 2, "start_x": 1, "start_y": 2, "end_x": 30, "end_y": 40}
        assert _summarize_params(drag) == "(1, 2) (1,2)→(30,40)"

    def test_custom_tool_coordinates_fall_through(self):
        # A custom tool's own x/y can be any type, or unpaired: never int()'d
        # (used to raise and lose the whole report), never silently dropped.
        assert _summarize_params({"x": "B", "y": "7"}) == "x=B y=7"
        assert _summarize_params({"x": 5}) == "x=5"
        assert _summarize_params({"x": True, "y": 3}) == "x=True y=3"
        assert _summarize_params({"start_x": 1, "end_x": 2}) == "start_x=1 end_x=2"

    def test_non_numeric_coordinates_render_in_report(self, tmp_path):
        log = [_entry("a", action_type="teleport", params={"x": "B", "y": "7"})]
        assert "x=B y=7" in _render(tmp_path, log)


class TestTitle:
    def test_explicit_task_name_wins(self, tmp_path):
        html = _render(tmp_path, [_entry("Open the menu")], title="Smoke test")
        assert "<h1 class='clamp'>Smoke test</h1>" in html
        assert "<title>Smoke test — Qirabot report</title>" in html
        # The instruction is not the title, so it always gets its toggle.
        assert "<details class='instr'><summary>Full instruction · 13 chars" in html

    def test_single_task_titles_from_its_first_line(self, tmp_path):
        text = "Open the menu\nthen tap Save"
        html = _render(tmp_path, [_entry(text)])
        assert ">Open the menu</h1>" in html
        # Multi-line: the toggle always shows.
        assert "<details class='instr'><summary>Full instruction · 2 lines, 27 chars" in html

    def test_single_line_toggle_is_left_to_the_browser(self, tmp_path):
        # Whether one long line was clamped depends on the viewport: the
        # toggle starts hidden ("auto") and _PAGE_JS reveals it.
        html = _render(tmp_path, [_entry("x" * 300)])
        assert "<details class='instr auto'>" in html

    def test_repeat_run_titles_from_its_instruction_not_its_key(self, tmp_path):
        html = _render(
            tmp_path, [_entry("Log in #2")], instructions={"Log in #2": "Log in"}
        )
        assert ">Log in</h1>" in html

    def test_several_tasks_count(self, tmp_path):
        html = _render(tmp_path, [_entry("a"), _entry("b"), _entry("c")])
        assert "<h1>3 tasks</h1>" in html

    def test_manual_only_run(self, tmp_path):
        assert "<h1>Manual steps</h1>" in _render(tmp_path, [_entry("setup")])


class TestMeta:
    def test_time_device_and_run_id(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a", ts=1000.0)],
            task_id="f23fbe53",
            device=("browser", 1280, 720),
        )
        assert "<span>browser · 1280×720</span>" in html
        assert "Run <b class='mono'>f23fbe53</b>" in html

    def test_no_device_no_device_item(self, tmp_path):
        html = _render(tmp_path, [_entry("a")], task_id="f23fbe53")
        meta = html.split("<div class='meta'>")[1].split("</div>")[0]
        assert "×" not in meta


class TestOutcomeZone:
    def test_single_pass(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a")],
            outcomes={"a": "completed"},
            stats={"total_steps": 1, "ai_steps": 1},
        )
        assert "<div class='zone outcome'>" in html
        assert "<span class='t'>Passed</span>" in html
        assert "<b>1</b>/1 passed · <b>1</b> step, all AI" in html

    def test_single_task_names_its_status(self, tmp_path):
        html = _render(tmp_path, [_entry("a")], outcomes={"a": "max_duration"})
        assert "<div class='zone outcome warn'>" in html
        assert "<span class='t'>Max duration</span>" in html

    def test_only_truncation_is_amber(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a"), _entry("b")],
            outcomes={"a": "completed", "b": "max_steps"},
        )
        assert "<div class='zone outcome warn'>" in html
        assert "<b>1</b>/2 passed · 1 truncated" in html

    def test_any_real_failure_is_red(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a"), _entry("b"), _entry("c")],
            outcomes={"a": "completed", "b": "max_steps", "c": "goal_failed"},
        )
        assert "<div class='zone outcome fail'>" in html
        assert "<span class='t'>1 failed</span>" in html
        assert "<b>1</b>/3 passed · 1 truncated" in html

    def test_split_section_counts_once(self, tmp_path):
        log = [_entry("a"), _entry("setup"), _entry("a")]
        html = _render(tmp_path, log, outcomes={"a": "completed"})
        assert "<b>1</b>/1 passed" in html

    def test_mixed_steps_count(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("setup"), _entry("setup")],
            stats={"total_steps": 2, "ai_steps": 0},
        )
        assert "<span class='t'>No AI tasks</span>" in html
        assert "<b>2</b> steps, 0 AI" in html


class TestTimeZone:
    def test_working_and_lost_split(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a")],
            stats={"step_duration_ms": 100_000, "model_ms": 40_000,
                   "rate_limit_wait_ms": 20_000, "timeout_wait_ms": 5_000},
        )
        assert "<span class='v'>1m40s</span>" in html
        assert "25.0s lost to capacity (25%)" in html
        assert "<div class='bar'>" in html
        # Device is the wall clock outside the model and the waits.
        assert "<i class='device-bg'></i>Device<b>35.0s</b>" in html
        assert "<span class='z'><i class='outage-bg'></i>Outage<b>0</b></span>" in html

    def test_no_ledger_shows_the_wall_clock_only(self, tmp_path):
        # An injected provider keeps no ledger: a bar of pure device time
        # would misread.
        html = _render(tmp_path, [_entry("a")], stats={"step_duration_ms": 10_000})
        assert "<span class='v'>10.0s</span>" in html
        assert "<div class='bar'>" not in html
        assert "lost to capacity" not in html

    def test_no_time_no_zone(self, tmp_path):
        assert "<div class='zl'>Time</div>" not in _render(tmp_path, [_entry("a")])


class TestModelZone:
    def test_model_and_tier_with_their_notes(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a")],
            model="gemini-vertex/gemini-3.5-flash — 2 calls served by fallback x",
            tier="flex — some calls served as priority",
        )
        assert "<span class='main mono'>gemini-vertex/gemini-3.5-flash</span>" in html
        assert "<span class='note'>2 calls served by fallback x</span>" in html
        assert "<dt>Tier</dt><dd><span class='main'>flex</span>" in html

    def test_default_tier_note_is_not_a_warning(self, tmp_path):
        html = _render(tmp_path, [_entry("a")], tier="standard — default")
        assert (
            "<dt>Tier</dt><dd><span class='main'>standard</span>"
            "<span class='note plain'>default</span></dd>"
        ) in html

    def test_no_tier_label_no_row(self, tmp_path):
        # An external caller may pass none.
        html = _render(tmp_path, [_entry("a")], model="gemini-vertex/gemini-3.5-flash")
        assert "<dt>Tier</dt>" not in html

    def test_tokens_without_breakdown_are_plain(self, tmp_path):
        html = _render(
            tmp_path, [_entry("a")], stats={"input_tokens": 1000, "output_tokens": 500}
        )
        assert "<dt>Tokens</dt><dd><span class='main'>1.5k</span>" in html
        assert "id='tokbtn'" not in html
        assert "id='tokpop'" not in html


class TestTokenPopover:
    def _html(self, tmp_path):
        return _render(
            tmp_path,
            [_entry("a")],
            stats={"input_tokens": 9_000, "cache_read_tokens": 1_000,
                   "output_tokens": 3_000, "thinking_tokens": 1_000},
            tokens_by={
                ("gemini-3.5-flash", "standard"): {
                    "calls": 4, "input": 8_000, "cached": 1_000,
                    "output": 2_500, "thinking": 1_000,
                },
                # A failed call: no answering tier.
                ("gemini-3.5-flash", ""): {
                    "calls": 1, "input": 1_000, "cached": 0, "output": 500, "thinking": 0,
                },
            },
        )

    def test_total_opens_the_breakdown(self, tmp_path):
        html = self._html(tmp_path)
        assert "id='tokbtn' aria-expanded='false' aria-controls='tokpop'" in html
        assert "13.0k <span class='car'>▾</span></button>" in html
        assert "<div class='tokpop' id='tokpop' role='dialog'" in html

    def test_rows_split_thinking_out_of_response(self, tmp_path):
        html = self._html(tmp_path)
        # 2.5k output of which 1k thinking -> 1.5k response; Total adds up.
        assert (
            "<td class='num mono'>4</td><td class='num mono'>8.0k</td>"
            "<td class='num mono'>1.0k</td><td class='num mono'>1.5k</td>"
            "<td class='num mono'>1.0k</td><td class='num mono'>11.5k</td>"
        ) in html

    def test_unknown_tier_reads_as_a_dash(self, tmp_path):
        html = self._html(tmp_path)
        assert (
            "<td class='mono'>gemini-3.5-flash</td><td><span class='dim'>—</span></td>"
        ) in html

    def test_footer_totals(self, tmp_path):
        html = self._html(tmp_path)
        assert "<tr><td colspan='2'>Total</td><td class='num mono'>5</td>" in html
        assert "<td class='num mono'>13.0k</td></tr></tfoot>" in html


class TestTaskOverview:
    def test_one_row_per_task(self, tmp_path):
        log = [
            _ai_entry("a"),
            _ai_entry("Log in #2"),
            _ai_entry("Log in #2"),
        ]
        html = _render(
            tmp_path,
            log,
            outcomes={"a": "completed", "Log in #2": "goal_failed"},
            instructions={"a": "a", "Log in #2": "Log in\nwith the test account"},
            section_stats={"Log in #2": {"step_duration_ms": 8_000, "model_ms": 6_000}},
        )
        assert "<div class='tasks'>" in html
        assert (
            "<a href='#task-1'><span class='i'>1</span><span class=\"badge pass\">PASS</span>"
            "<span class='n' title='a'>a</span><span class='r'>1 step</span>"
        ) in html
        assert (
            "<a href='#task-2'><span class='i'>2</span><span class=\"badge fail\">FAIL</span>"
            "<span class='n' title='Log in\nwith the test account'>Log in</span>"
            "<span class='r'>2 steps</span><span class='r mono'>8.0s</span><div class='mb'>"
        ) in html
        assert "<section id='task-2'>" in html

    def test_single_task_has_no_overview(self, tmp_path):
        html = _render(tmp_path, [_entry("a")], outcomes={"a": "completed"})
        assert "<div class='tasks'>" not in html

    def test_unknown_status_falls_back_to_fail(self, tmp_path):
        html = _render(
            tmp_path, [_entry("a"), _entry("b")], outcomes={"a": "someday-new"}
        )
        assert "<span class='i'>1</span><span class=\"badge fail\">FAIL</span>" in html
        # Unjudged: a neutral dash, never a verdict.
        assert "<span class='i'>2</span><span class=\"badge neutral\">—</span>" in html


class TestSections:
    def test_single_task_is_headed_steps(self, tmp_path):
        html = _render(tmp_path, [_entry("a")], outcomes={"a": "max_steps"})
        assert (
            "<h2><div class='row'>Steps <span class=\"badge warn\">MAX STEPS</span>"
            "<span class='sub'>1 step</span></div></h2>"
        ) in html

    def test_interleaved_manual_steps_stay_in_execution_order(self, tmp_path):
        # manual → ai(a) → manual → ai(b) renders as four sections in that
        # order, not one merged manual block up front.
        log = [_entry("setup"), _entry("a"), _entry("setup"), _entry("b")]
        html = _render(tmp_path, log, outcomes={"a": "completed", "b": "completed"})
        first_manual = html.index("<h2><div class='row'>Manual steps")
        task_1 = html.index("<h2><div class='row'>Task 1")
        second_manual = html.index("<h2><div class='row'>Manual steps", first_manual + 1)
        task_2 = html.index("<h2><div class='row'>Task 2")
        assert first_manual < task_1 < second_manual < task_2

    def test_tasks_show_a_clamped_instruction(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a"), _entry("b")],
            instructions={"a": "Open the menu\nthen tap Save", "b": "b"},
        )
        assert (
            "<div class='ins clamp' title='Open the menu\nthen tap Save'>Open the menu</div>"
        ) in html

    def test_group_time_sums_its_steps(self, tmp_path):
        html = _render(tmp_path, [_ai_entry("a"), _ai_entry("a", duration_ms=1_500)])
        assert "<span class='sub'>2 steps · 5.5s</span>" in html

    def test_error_banner_renders_only_on_last_group(self, tmp_path):
        log = [_entry("a"), _entry("setup"), _entry("a")]
        html = _render(
            tmp_path,
            log,
            outcomes={"a": "error"},
            section_errors={"a": "session expired"},
        )
        assert html.count("✗ session expired") == 1
        assert html.index("✗ session expired") > html.rindex("<h2><div class='row'>Task 1")

    def test_max_steps_banner_renders_amber(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a")],
            outcomes={"a": "max_steps"},
            section_errors={"a": "max steps reached (5)"},
        )
        assert "<div class='notice'>⚠ max steps reached (5)</div>" in html

    def test_error_banner_renders_red(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a")],
            outcomes={"a": "error"},
            section_errors={"a": "session expired"},
        )
        assert "<div class='notice error'>✗ session expired</div>" in html

    def test_error_only_section_still_renders(self, tmp_path):
        # A section that failed before recording any step still shows its
        # banner and badge — otherwise the failure reason vanishes.
        html = _render(
            tmp_path,
            [_entry("a")],
            outcomes={"a": "completed", "b": "error"},
            section_errors={"b": "AI request failed"},
        )
        assert "AI request failed" in html
        assert "<span class='i'>2</span><span class=\"badge error\">" not in html
        assert "<span class='i'>2</span><span class=\"badge fail\">ERROR</span>" in html


class TestStepTable:
    def test_columns(self, tmp_path):
        html = _render(tmp_path, [_entry("a")])
        assert (
            "<th>#</th><th>Time</th><th>Action</th><th>Detail</th><th>Model</th>"
            "<th class='num'>Duration</th><th class='num'>Waits</th><th>Screenshot</th>"
        ) in html

    def test_manual_step_shows_dashes(self, tmp_path):
        rows = _rows(_render(tmp_path, [_entry("setup")]))
        dash = "<span class='none'>—</span>"
        assert f"<td class='mdl'>{dash}</td>" in rows
        assert f"<td class='num'>{dash}</td>" in rows
        assert f"<td class='waits'>{dash}</td>" in rows

    def test_ai_step_duration_with_its_breakdown(self, tmp_path):
        rows = _rows(_render(tmp_path, [_ai_entry("a")]))
        assert "<div class='dur mono'>4.0s</div><div class='minibar'>" in rows
        assert "title='Model 3.0s'" in rows
        assert "title='Device 1.0s'" in rows

    def test_waits_get_chips_and_a_hot_row(self, tmp_path):
        rows = _rows(_render(
            tmp_path,
            [_ai_entry("a", duration_ms=70_000, rate_limit_wait_ms=42_000,
                       timeout_wait_ms=20_000)],
        ))
        assert "<tr class='hot'>" in rows
        assert "<span class='wchip rate mono'>rate limit 42.0s</span>" in rows
        assert "<span class='wchip timeout mono'>timeout 20.0s</span>" in rows
        assert "wchip outage" not in rows

    def test_default_model_is_muted_others_highlighted(self, tmp_path):
        rows = _rows(_render(
            tmp_path,
            [_ai_entry("a"), _ai_entry("a", model="gemini-3.5-pro", tier="priority")],
            default_model="gemini-3.5-flash",
        ))
        assert "<td class='mdl mono same'>gemini-3.5-flash</td>" in rows
        # A non-default tier gets a pill.
        assert (
            "<td class='mdl mono diff'>gemini-3.5-pro<br>"
            "<span class='tier priority'>priority</span></td>"
        ) in rows

    def test_tier_pill_follows_the_configured_tier(self, tmp_path):
        rows = _rows(_render(
            tmp_path, [_ai_entry("a", tier="flex")], tier="flex", default_model="gemini-3.5-flash"
        ))
        assert "<td class='mdl mono same'>gemini-3.5-flash</td>" in rows

    def test_warn_entry_renders_amber_not_red(self, tmp_path):
        log = [_entry("a", action_type="ai", output="max steps reached",
                      finished=True, success=False, warn=True)]
        rows = _rows(_render(tmp_path, log, outcomes={"a": "max_steps"}))
        assert "<tr class='warn-row'>" in rows
        assert "fail-row" not in rows
        assert "ai ⚠" in rows

    def test_failed_entry_renders_red(self, tmp_path):
        rows = _rows(_render(
            tmp_path, [_entry("a", success=False, output="boom")], outcomes={"a": "error"}
        ))
        assert "<tr class='fail-row'>" in rows
        assert "click ✗" in rows

    def test_finished_entry_gets_a_check(self, tmp_path):
        rows = _rows(_render(tmp_path, [_entry("a", action_type="done", finished=True)]))
        assert "done <span class='done'>✓</span>" in rows


class TestDoneStep:
    ANSWER = "Found 2 videos:\n\n1. **A**\n   - views: 5M\n2. **B**"

    def test_result_and_success_are_not_repeated(self, tmp_path):
        log = [_entry("a", action_type="done", finished=True, output=self.ANSWER,
                      params={"result": self.ANSWER, "success": True})]
        rows = _rows(_render(tmp_path, log))
        assert "result=" not in rows
        assert "success=" not in rows
        assert rows.count("views: 5M") == 1

    def test_a_result_that_differs_from_the_output_stays(self, tmp_path):
        log = [_entry("a", action_type="done", finished=True, output="short",
                      params={"result": "long version", "success": False})]
        rows = _rows(_render(tmp_path, log))
        assert "result=long version" in rows
        assert "success=" not in rows

    def test_other_actions_keep_every_param(self, tmp_path):
        log = [_entry("a", action_type="gm", params={"result": "x", "success": True},
                      output="x")]
        assert "result=x success=True" in _rows(_render(tmp_path, log))

    def test_output_renders_markdown(self, tmp_path):
        log = [_entry("a", action_type="done", finished=True, output=self.ANSWER)]
        rows = _rows(_render(tmp_path, log))
        assert "<div class='out md'>" in rows
        assert "<strong>A</strong>" in rows
        assert "**" not in rows

    def test_json_output_stays_verbatim(self, tmp_path):
        log = [_entry("a", action_type="extract", output='{\n  "a": "**b**"\n}')]
        rows = _rows(_render(tmp_path, log))
        assert "<div class='out mono'>{\n  &quot;a&quot;: &quot;**b**&quot;\n}</div>" in rows


class TestMarkdown:
    def test_paragraphs_and_line_breaks(self):
        assert _render_markdown("one\ntwo\n\nthree") == "<p>one<br>two</p><p>three</p>"

    def test_nested_lists(self):
        md = "1. **A**\n   - x: 1\n   - y: 2\n\n2. **B**\n   - z"
        assert _render_markdown(md) == (
            "<ol><li><strong>A</strong><ul><li>x: 1</li><li>y: 2</li></ul></li>"
            "<li><strong>B</strong><ul><li>z</li></ul></li></ol>"
        )

    def test_ordered_list_keeps_its_start(self):
        assert _render_markdown("3. c\n4. d") == "<ol start='3'><li>c</li><li>d</li></ol>"

    def test_heading_and_code(self):
        assert _render_markdown("## Result\nrun `ls -a`") == (
            "<p><strong>Result</strong></p><p>run <code>ls -a</code></p>"
        )

    def test_list_then_paragraph(self):
        assert _render_markdown("- a\n- b\nafter") == "<ul><li>a</li><li>b</li></ul><p>after</p>"

    def test_markup_is_escaped(self):
        out = _render_markdown("<script>x</script> [l](javascript:y) `<b>` **<i>**")
        assert "<script>" not in out and "<b>" not in out and "<i>" not in out
        assert "&lt;script&gt;" in out
        assert "<code>&lt;b&gt;</code>" in out
        assert "<strong>&lt;i&gt;</strong>" in out

    def test_identifiers_are_left_alone(self):
        assert _render_markdown("call __init__ and a*b*c") == "<p>call __init__ and a*b*c</p>"


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
        html = _render(tmp_path, log)
        # Once in the step cell, once in the lightbox label for that shot.
        assert html.count("frame from an earlier step") == 2

    def test_fresh_entries_carry_no_label(self, tmp_path):
        log = [_entry("a", thumb="data:image/jpeg;base64,x")]
        assert "frame from an earlier step" not in _render(tmp_path, log)


class TestStepTimestamps:
    def test_no_video_shows_the_clock_only(self, tmp_path):
        # An offset from the run start would only restate the clock time.
        html = _render(tmp_path, [_entry("a", ts=1000.0), _entry("a", ts=1092.0)])
        assert "+0:00" not in html
        assert "+1:32" not in html
        assert "seekTo" not in html
        assert time.strftime("%H:%M:%S", time.localtime(1092.0)) in html

    def test_offsets_seek_video_when_recording_start_known(self, tmp_path):
        html = _render(
            tmp_path,
            [_entry("a", ts=1005.5)],
            recording="recording.mp4",
            recording_start=1000.0,
        )
        assert "seekTo(5.5)" in html
        assert "+0:05" in html

    def test_recording_without_start_ts_gets_no_offsets(self, tmp_path):
        # Recording present but its start time unknown (e.g. an external
        # recording.mp4 dropped into report_dir): a run-relative offset
        # would not match the video's own timeline, so none is shown.
        log = [_entry("a", ts=1000.0), _entry("a", ts=1007.0)]
        html = _render(tmp_path, log, recording="recording.mp4")
        assert "+0:07" not in html
        assert "onclick='return seekTo" not in html

    def test_entries_without_ts_render_blank_time_cell(self, tmp_path):
        rows = _rows(_render(tmp_path, [_entry("a")]))
        assert "<td class='when mono'></td>" in rows


class TestLongDecision:
    def test_table_clamps_decision_behind_toggle(self, tmp_path):
        html = _render(tmp_path, [_entry("a", decision="很长的推理" * 200)])
        rows = _rows(html)
        assert "<div class='dec clip'>" in rows
        # Hidden until the page script measures that the clamp actually cut.
        assert "<button type='button' class='more' hidden>Show more</button>" in rows

    def test_lightbox_keeps_full_unclamped_decision(self, tmp_path):
        html = _render(
            tmp_path, [_entry("a", decision="很长的推理" * 200, thumb="data:image/jpeg;base64,x")]
        )
        lightbox = html[html.rindex("</table>"):]
        assert "很长的推理" * 200 in lightbox
        assert "dec clip" not in lightbox

    def test_no_decision_no_toggle(self, tmp_path):
        assert "class='more'" not in _rows(_render(tmp_path, [_entry("a", decision="")]))


class TestRecordingPlayer:
    def test_player_wraps_steps_for_side_by_side_layout(self, tmp_path):
        html = _render(
            tmp_path, [_entry("a", ts=1005.5)], recording="recording.mp4", recording_start=1000.0
        )
        # One wrapper holding the player then the steps, closed before the
        # lightbox, so a portrait video can sit in a column beside them.
        media = html.index("<div class='media'>")
        assert media < html.index("<video") < html.index("<div class='main'>") < html.index("<section")
        # Last section, then .main, .media, .wrap.
        assert "</table></div></section></div></div></div>" in html
        assert "class='ticks'" in html
        assert "window.seekTo" in html

    def test_seekable_rows_carry_offset_and_label(self, tmp_path):
        log = [_entry("a", ts=1005.5, action_type="tap"), _entry("a", ts=999.0)]
        html = _render(tmp_path, log, recording="recording.mp4", recording_start=1000.0)
        assert "data-t='5.5' data-l='Steps · 1 tap'" in html
        # A step from before the recording started has no place on its timeline.
        assert html.count("data-t=") == 1

    def test_recording_without_start_ts_has_no_markers(self, tmp_path):
        html = _render(tmp_path, [_entry("a", ts=1000.0)], recording="recording.mp4")
        assert "<video" in html
        assert "class='ticks'" not in html
        assert "data-t=" not in html

    def test_no_recording_no_player(self, tmp_path):
        html = _render(tmp_path, [_entry("a", ts=1000.0)])
        assert "<div class='media'>" not in html
        assert "window.seekTo" not in html


class TestStatusVocabulary:
    def test_badge_map_covers_run_status_exactly(self):
        # qirabot.client.RunStatus is the single status vocabulary; the
        # report's badge map must cover it exactly — a new status added there
        # without a badge here would render as a misleading FAIL fallback.
        from typing import get_args

        from qirabot.client import RunStatus
        from qirabot.report import _STATUS_KINDS

        assert set(_STATUS_KINDS) == set(get_args(RunStatus))
