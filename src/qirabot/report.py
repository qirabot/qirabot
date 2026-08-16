"""Self-contained HTML run report.

Pure rendering — no model calls, no network. Builds an HTML file from the
session step log that :class:`qirabot.Qirabot` accumulates during a run and
hands over at render time (each entry is one step dict; see ``write_html``):

    write_html(log_entries, "report.html", title=..., outcomes=..., recording=...)

Each step carries an embedded thumbnail (base64 data URI) so the report is a
single self-contained file, plus a link to the full-resolution screenshot under
``screenshots/`` when one was saved.
"""

from __future__ import annotations

import html
import json
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_CSS = """
:root { color-scheme: light dark; }
body { font: 14px/1.5 -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
       margin: 0; padding: 24px; background: #f6f7f9; color: #1a1a1a; }
h1 { font-size: 20px; margin: 0 0 4px; }
.meta { color: #666; font-size: 12px; margin-bottom: 16px; }
.stats { color: #555; font-size: 12px; margin: -10px 0 16px;
         font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.tally { margin-bottom: 10px; }
.tally .badge { font-size: 14px; padding: 5px 14px; }
/* One judged task per grid row: status badge, then name. The status column
   is max-content, so it auto-sizes to the widest badge and every badge (and
   every name) lines up exactly, whatever width the font gives "MAX STEPS". */
.summary { display: grid; grid-template-columns: max-content minmax(0, 1fr);
           gap: 6px 10px; align-items: center; justify-items: start;
           margin-bottom: 20px; }
.badge { padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 600;
         white-space: nowrap; }
.summary .badge { justify-self: stretch; text-align: center; }
.summary .tname { font-size: 13px; max-width: 80ch; overflow: hidden;
                  text-overflow: ellipsis; white-space: nowrap; }
.pass { background: #d7f5dd; color: #06632b; }
.fail { background: #fbdcdc; color: #8a1010; }
.warn { background: #fdeec8; color: #7a5200; }
.neutral { background: #e6e8eb; color: #444; }
.notice { background: #fff4d6; color: #7a5200; padding: 10px 14px; border-radius: 8px;
          margin-bottom: 18px; font-size: 13px; }
.notice.error { background: #fbdcdc; color: #8a1010; }
section .notice { margin: 10px 14px 4px; }
section { background: #fff; border: 1px solid #e3e5e8; border-radius: 10px;
          margin-bottom: 18px; overflow: hidden; }
section > h2 { font-size: 15px; margin: 0; padding: 12px 16px; background: #fafbfc;
              border-bottom: 1px solid #eceef0; display: flex; gap: 10px; align-items: center; }
.steps { display: grid; grid-template-columns: 48px 96px 160px 1fr 200px; gap: 0; }
.steps > div { padding: 10px 12px; border-bottom: 1px solid #f0f1f3; }
.when { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px;
        color: #888; white-space: nowrap; }
.when a { color: #0b62d6; text-decoration: none; }
.when a:hover { text-decoration: underline; }
.head { font-weight: 600; color: #888; font-size: 11px; text-transform: uppercase;
        background: #fcfcfd; }
/* Custom tool names (e.g. capture_lqa_frame) can exceed the fixed column;
   underscores aren't break points, so allow breaking anywhere. */
.act { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 600;
       overflow-wrap: anywhere; }
.steps > div.fail-row { background: #fff5f5; }
.steps > .act.fail-row { color: #8a1010; }
.steps > div.warn-row { background: #fffaef; }
.steps > .act.warn-row { color: #7a5200; }
.detail { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
          color: #333; white-space: pre-wrap; word-break: break-word; }
.detail .out { color: #555; }
.detail .decision { display: block; color: #777; font-style: italic; font-size: 11px;
                    font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
                    margin-bottom: 4px; }
.shot img { width: 100%; border-radius: 6px; border: 1px solid #e3e5e8; display: block;
            transition: transform .08s ease; }
.shot a { display: block; cursor: zoom-in; }
.shot a:hover img { transform: scale(1.02); }
video { max-width: 100%; max-height: 70vh; display: block; border-radius: 8px;
        margin-bottom: 18px; }
/* Lightbox */
.lightbox { position: fixed; inset: 0; z-index: 1000; background: rgba(0,0,0,.88);
            display: none; }
.lightbox.open { display: flex; flex-direction: column; }
.lb-bar { display: flex; gap: 12px; align-items: baseline; padding: 12px 18px;
          color: #f0f0f0; background: rgba(0,0,0,.45); }
.lb-label { color: #9aa0a6; font-size: 12px; }
.lb-act { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-weight: 700; }
.lb-count { color: #9aa0a6; font-size: 12px; margin-left: auto; }
.lb-close { cursor: pointer; font-size: 18px; line-height: 1; color: #f0f0f0; padding: 0 4px; }
.lb-close:hover { color: #fff; }
.lb-stage { flex: 1; position: relative; display: flex; align-items: center;
            justify-content: center; overflow: auto; padding: 16px; }
.lb-stage img { max-width: 100%; max-height: 100%; object-fit: contain; border-radius: 6px;
                box-shadow: 0 10px 40px rgba(0,0,0,.5); }
.lb-nav { position: absolute; top: 50%; transform: translateY(-50%); cursor: pointer;
          font-size: 44px; line-height: 1; color: #fff; opacity: .55; user-select: none;
          padding: 8px 14px; -webkit-user-select: none; }
.lb-nav:hover { opacity: 1; }
.lb-prev { left: 8px; }
.lb-next { right: 8px; }
.lb-caption { padding: 12px 18px; color: #d8dadd; background: rgba(0,0,0,.45);
              font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px;
              white-space: pre-wrap; word-break: break-word; max-height: 22vh; overflow: auto; }
.lb-caption:empty { display: none; }
.lb-caption .out { color: #a9c7ff; }
/* Narrow screens: shrink the fixed columns and let the step table scroll
   sideways instead of overflowing the page. */
@media (max-width: 720px) {
  body { padding: 12px; }
  .steps { overflow-x: auto;
           grid-template-columns: 36px 84px 96px minmax(180px, 1fr) 140px; }
}
@media (prefers-color-scheme: dark) {
  body { background: #16181c; color: #e8e8e8; }
  section { background: #1e2126; border-color: #2c3036; }
  section > h2 { background: #22262c; border-color: #2c3036; }
  .head { background: #1a1d22; }
  .steps > div { border-color: #262a30; }
  .steps > div.fail-row { background: #3a1d1d; }
  .steps > .act.fail-row { color: #f3a6a6; }
  .warn { background: #3a2f12; color: #f0d493; }
  .steps > div.warn-row { background: #332a14; }
  .steps > .act.warn-row { color: #e8c877; }
  .stats { color: #9aa0a6; }
  .when a { color: #a9c7ff; }
  .detail .decision { color: #9aa0a6; }
  .notice { background: #3a2f12; color: #f0d493; }
  .notice.error { background: #3a1d1d; color: #f3a6a6; }
}
"""


_COORD_KEYS = ("x", "y", "start_x", "start_y", "end_x", "end_y")


def _summarize_params(params: dict[str, Any]) -> str:
    """A compact one-line view of an action's parameters.

    Known keys get pretty formatting; everything else falls through to a
    generic ``key=value`` so a new action type never renders blank.
    """
    parts: list[str] = []
    rendered: set[str] = set()

    # Primary human-readable descriptor.
    for key in ("locate", "instruction", "assertion", "url"):
        if params.get(key):
            parts.append(str(params[key]))
            rendered.add(key)
            break

    if params.get("text"):
        parts.append(f'text="{params["text"]}"')
    rendered.add("text")

    # Scroll: keep the compact "down 500" form rather than two key=value pairs.
    if params.get("direction"):
        parts.append(f'{params["direction"]} {params.get("amount", "")}'.strip())
        rendered.update(("direction", "amount"))

    # Coordinates, formatted compactly.
    x, y = params.get("x"), params.get("y")
    if x is not None and y is not None:
        parts.append(f"({int(x)}, {int(y)})")
    sx, sy, ex, ey = (params.get(k) for k in _COORD_KEYS[2:])
    if sx is not None and sy is not None and ex is not None and ey is not None:
        parts.append(f"({int(sx)},{int(sy)})→({int(ex)},{int(ey)})")
    rendered.update(_COORD_KEYS)

    # Everything else: generic fallback so nothing silently vanishes.
    for key, val in params.items():
        # Explicit checks, not `val in (None, "", False)`: 0 == False in
        # Python, so a membership test would silently drop numeric zeros.
        if key in rendered or val is None or val == "" or val is False:
            continue
        parts.append(f"{key}={val}")

    return " ".join(parts)


def format_action_details(
    params: Mapping[str, Any], *, locate_max: int = 0, text_max: int = 0
) -> list[str]:
    """Human-readable fragments of an action's key params, in display order:
    the locate description, typed text, scroll direction+amount.

    Public and shared by the CLI's step lines and the overlay's step body so
    the two live surfaces never drift on which params matter or how they
    read. ``locate_max``/``text_max`` > 0 clip those unbounded user/model
    fields (the overlay's fixed window needs that; a terminal doesn't).
    The report's own step table uses the exhaustive :func:`_summarize_params`
    instead — nothing may silently vanish from the archived artifact.
    """

    def _fmt(value: Any, limit: int) -> str:
        text = str(value)
        if limit <= 0:
            return text
        text = " ".join(text.split())
        return text if len(text) <= limit else text[: limit - 1] + "…"

    parts: list[str] = []
    if "locate" in params:
        parts.append(f'"{_fmt(params["locate"], locate_max)}"')
    if "text" in params:
        parts.append(f'← "{_fmt(params["text"], text_max)}"')
    if "direction" in params:
        parts.append(f"{params['direction']} {params.get('amount', '')}".rstrip())
    return parts


def _badge(label: str, kind: str) -> str:
    return f'<span class="badge {kind}">{html.escape(label)}</span>'


# RunStatus -> (badge label, css class). Unknown statuses fall back to FAIL —
# a status we don't recognize should read as broken, never silently green.
_STATUS_KINDS = {
    "completed": ("PASS", "pass"),
    "goal_failed": ("FAIL", "fail"),
    "max_steps": ("MAX STEPS", "warn"),
    "error": ("ERROR", "fail"),
    # A deliberate user abort (ESC hold / mouse-to-corner / Ctrl+C via
    # cancel()): amber, not red — the bot didn't fail, the user stopped it.
    "cancelled": ("CANCELLED", "warn"),
}


def _normalize_status(value: bool | str) -> str:
    """Map legacy bool outcomes onto the status vocabulary.

    ``outcomes`` historically held ``dict[str, bool]``; external callers of
    :func:`write_html` may still pass bools, which carried no more meaning
    than pass/fail.
    """
    if value is True:
        return "completed"
    if value is False:
        return "goal_failed"
    return str(value)


def fmt_tokens(n: int) -> str:
    """Compact token count: 1234 -> ``1.2k``, 980 -> ``980``.

    Public: the CLI renders its usage summary line with the same formatting
    so the two surfaces never drift.
    """
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _fmt_offset(secs: float) -> str:
    """Elapsed offset for the time column: ``+0:07`` / ``+1:32`` / ``+1:02:05``."""
    s = int(secs)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"+{h}:{m:02d}:{sec:02d}"
    return f"+{m}:{sec:02d}"


def fmt_ms(ms: int) -> str:
    """Human duration from milliseconds: ``820ms`` / ``23.4s`` / ``2m03s`` / ``1h05m``.

    Public for the same reason as :func:`fmt_tokens`.
    """
    if ms < 1000:
        return f"{ms}ms"
    secs = ms / 1000
    if secs < 60:
        return f"{secs:.1f}s"
    mins = int(secs // 60)
    if mins < 60:
        return f"{mins}m{int(secs % 60):02d}s"
    return f"{mins // 60}h{mins % 60:02d}m"


def _render_stats(stats: dict[str, int], model: str, tier: str = "") -> str:
    """A one-line run summary (steps · tokens · timing · model · tier).

    The headline count is ``total_steps`` — every timeline entry — with the
    AI-decision subset in parentheses.
    Returns an empty string only when nothing ran at all: a purely local run
    (0 AI steps) still gets its step count.
    """
    total_steps = stats.get("total_steps", 0)
    if not total_steps:
        return ""
    inp = stats.get("input_tokens", 0)
    out = stats.get("output_tokens", 0)
    think = stats.get("thinking_tokens", 0)
    cache = stats.get("cache_read_tokens", 0) + stats.get("cache_write_tokens", 0)
    # input_tokens is the non-cached prompt portion only; the cached portion
    # rides in the cache counters, so the total needs both. Thinking is
    # already counted within output tokens (Anthropic semantics; the Gemini
    # provider normalizes to match) — do not add it again.
    total = inp + cache + out
    bits = [f"{total_steps} steps ({stats.get('ai_steps', 0)} AI)"]
    if total:
        detail = [f"in {fmt_tokens(inp)}"]
        if cache:
            detail.append(f"cache {fmt_tokens(cache)}")
        detail.append(f"out {fmt_tokens(out)}")
        if think:
            # Anthropic reports no separate thinking count (it stays 0 there)
            # — show the split only when a provider actually supplies it.
            detail.append(f"think {fmt_tokens(think)}")
        bits.append(f"{fmt_tokens(total)} tokens ({' / '.join(detail)})")
    if stats.get("step_duration_ms"):
        bits.append(fmt_ms(stats["step_duration_ms"]))
    if model:
        bits.append(f"model {model}")
    if tier:
        # Which tier was served decides the per-token rate, so it belongs
        # next to the token counts it reprices.
        bits.append(f"tier {tier}")
    return f"<div class='stats'>{html.escape(' · '.join(bits))}</div>"


_LIGHTBOX_JS = """
const SHOTS = __SHOTS__;
let lbIdx = 0;
const lb = document.getElementById('lightbox');
function renderLb() {
  const s = SHOTS[lbIdx];
  if (!s) return;
  document.getElementById('lb-img').src = s.src;
  document.getElementById('lb-img').alt = s.label;
  document.getElementById('lb-act').textContent = s.action;
  document.getElementById('lb-label').textContent = s.label;
  document.getElementById('lb-detail').innerHTML = s.detail;
  document.getElementById('lb-count').textContent = (lbIdx + 1) + ' / ' + SHOTS.length;
}
function openLb(i) { lbIdx = i; renderLb(); lb.classList.add('open'); return false; }
function closeLb() { lb.classList.remove('open'); }
function stepLb(d) {
  if (!SHOTS.length) return;
  lbIdx = (lbIdx + d + SHOTS.length) % SHOTS.length;
  renderLb();
}
lb.addEventListener('click', function (e) {
  const t = e.target;
  if (t === lb || t.id === 'lb-stage' || t.id === 'lb-close') closeLb();
});
document.getElementById('lb-prev').addEventListener('click', function (e) {
  e.stopPropagation(); stepLb(-1);
});
document.getElementById('lb-next').addEventListener('click', function (e) {
  e.stopPropagation(); stepLb(1);
});
document.addEventListener('keydown', function (e) {
  if (!lb.classList.contains('open')) return;
  if (e.key === 'Escape') closeLb();
  else if (e.key === 'ArrowLeft') stepLb(-1);
  else if (e.key === 'ArrowRight') stepLb(1);
});
"""


def _lightbox(shots: list[dict[str, str]]) -> str:
    """The lightbox overlay markup + script, with ``shots`` data inlined.

    Returns an empty string when there are no screenshots to view.
    """
    if not shots:
        return ""
    # json.dumps handles all string escaping; guard against a literal "</script>"
    # (or any "</...") inside the data prematurely closing the script element.
    data = json.dumps(shots, ensure_ascii=False).replace("</", "<\\/")
    script = _LIGHTBOX_JS.replace("__SHOTS__", data)
    return (
        "<div id='lightbox' class='lightbox' role='dialog' aria-modal='true'>"
        "<div class='lb-bar'>"
        "<span class='lb-label' id='lb-label'></span>"
        "<span class='lb-act' id='lb-act'></span>"
        "<span class='lb-count' id='lb-count'></span>"
        "<span class='lb-close' id='lb-close' title='Close (Esc)'>✕</span>"
        "</div>"
        "<div class='lb-stage' id='lb-stage'>"
        "<span class='lb-nav lb-prev' id='lb-prev' title='Previous (←)'>‹</span>"
        "<img id='lb-img' alt=''>"
        "<span class='lb-nav lb-next' id='lb-next' title='Next (→)'>›</span>"
        "</div>"
        "<div class='lb-caption' id='lb-detail'></div>"
        "</div>"
        f"<script>{script}</script>"
    )


def write_html(
    log: list[dict[str, Any]],
    path: str | Path,
    *,
    title: str = "",
    task_id: str = "",
    outcomes: Mapping[str, bool | str] | None = None,
    section_errors: Mapping[str, str] | None = None,
    recording: str = "",
    recording_start: float = 0.0,
    record_error: str = "",
    stats: dict[str, int] | None = None,
    model: str = "",
    tier: str = "",
) -> Path:
    """Render ``log`` to a self-contained HTML report at ``path``.

    ``section_errors`` maps a section to its failure text (max-steps
    truncation / terminal server error), rendered as a banner above that
    section's step table — these are section-level outcomes, not steps.

    ``recording_start`` is the epoch time the embedded recording began; when
    set (and a recording is present), each step's elapsed offset becomes a
    link that seeks the video to that moment.
    """
    outcomes = outcomes or {}
    section_errors = section_errors or {}
    stats = stats or {}
    # Callers predating the total_steps key still get a stats line keyed on
    # the timeline length.
    if stats and "total_steps" not in stats:
        stats = {**stats, "total_steps": len(log)}
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Group entries into runs of consecutive same-section steps, so the report
    # reads in execution order: manual steps interleaved between ai() tasks
    # stay where they happened instead of all merging into one block. The same
    # section name can therefore appear as several groups.
    groups: list[tuple[str, list[dict[str, Any]]]] = []
    for entry in log:
        sec = entry.get("section") or "setup"
        if not groups or groups[-1][0] != sec:
            groups.append((sec, []))
        groups[-1][1].append(entry)
    # A section that failed before recording any step (e.g. a terminal server
    # error on its first step) has a banner but no entries — still render it,
    # or the failure reason would vanish from the report.
    group_names = {name for name, _ in groups}
    for sec in section_errors:
        if sec not in group_names:
            groups.append((sec, []))
            group_names.add(sec)
    # Banners and badges attach to the *last* group of their section name:
    # errors/outcomes describe how that section's final run ended.
    last_group_of = {name: i for i, (name, _) in enumerate(groups)}

    def display_name(name: str) -> str:
        # "setup" is the client's section key for standalone (non-ai) actions;
        # now that those render in timeline order, "manual" describes them.
        return "manual" if name == "setup" else name

    def section_kind(name: str, entries: list[dict[str, Any]]) -> tuple[str, str]:
        if name in outcomes:
            status = _normalize_status(outcomes[name])
            return _STATUS_KINDS.get(status, ("FAIL", "fail"))
        return (f"{len(entries)} steps", "neutral")

    parts: list[str] = []
    parts.append("<!doctype html><html><head><meta charset='utf-8'>")
    parts.append(f"<title>{html.escape(title or 'Qirabot report')}</title>")
    parts.append(f"<style>{_CSS}</style></head><body>")
    parts.append(f"<h1>{html.escape(title or 'Qirabot run report')}</h1>")
    # Header timestamp: the run's first stamped step, so a report regenerated
    # from an old log still shows when the run happened. Falls back to "now"
    # for logs predating the "ts" field.
    first_ts = next((e["ts"] for e in log if e.get("ts")), 0.0)
    meta = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(first_ts) if first_ts else time.localtime(),
    )
    if task_id:
        meta += f" · task {html.escape(task_id)}"
    parts.append(f"<div class='meta'>{meta}</div>")
    parts.append(_render_stats(stats, model, tier))

    # Overall tally, on its own line: the report's headline answer ("how many
    # tasks ran, how many passed"), kept clear of the per-task badges below.
    # max_steps counts toward the total but not toward passed: a truncated run
    # isn't a pass, but as long as nothing truly failed the tally is amber
    # (raise the budget) rather than red (something broke). Each judged
    # section counts once, even when its steps split across several timeline
    # groups (outcomes are keyed per section).
    judged: list[str] = []
    seen_judged: set[str] = set()
    for name, _entries in groups:
        if name in outcomes and name not in seen_judged:
            seen_judged.add(name)
            judged.append(name)
    statuses = [_normalize_status(outcomes[name]) for name in judged]
    passed = sum(1 for st in statuses if st == "completed")
    truncated = sum(1 for st in statuses if st == "max_steps")
    total_judged = len(statuses)
    if total_judged:
        if passed == total_judged:
            kind = "pass"
        elif passed + truncated == total_judged:
            kind = "warn"
        else:
            kind = "fail"
        label = f"{passed}/{total_judged} passed"
        if truncated:
            label += f" · {truncated} truncated"
        parts.append(f"<div class='tally'>{_badge(label, kind)}</div>")
    # One line per judged task, in run order: an aligned status column, then
    # the task name. A section name can be an entire ai() instruction, so the
    # name is CSS-truncated with the full text on hover; unjudged (manual)
    # groups stay out of the summary — their step counts already show in the
    # stats line and on each section header.
    if judged:
        parts.append("<div class='summary'>")
        for name in judged:
            status = _normalize_status(outcomes[name])
            label, kind = _STATUS_KINDS.get(status, ("FAIL", "fail"))
            disp = html.escape(display_name(name))
            # Badge and name are direct grid children (no row wrapper): the
            # summary grid places each pair on its own row.
            parts.append(
                _badge(label, kind)
                + f'<span class="tname" title="{disp}">{disp}</span>'
            )
        parts.append("</div>")

    if recording:
        parts.append(
            f"<video controls src='{html.escape(recording)}'></video>"
            "<script>function seekTo(t){const v=document.querySelector('video');"
            "if(!v)return false;v.currentTime=t;"
            "v.scrollIntoView({behavior:'smooth',block:'center'});v.play();"
            "return false;}</script>"
        )
    elif record_error:
        parts.append(f"<div class='notice'>⚠ {html.escape(record_error)}</div>")

    # Step offsets are measured from the recording start when the video is
    # seekable, else from the first stamped step (≈ run start). Entries from
    # runs predating the "ts" field just leave the time cell empty.
    seekable = bool(recording) and recording_start > 0
    offset_base = recording_start if seekable else first_ts

    # Sections. Every step with a thumbnail also becomes a lightbox "shot" so the
    # viewer can page across all screenshots regardless of which section they're in.
    shots: list[dict[str, str]] = []
    for gi, (sec, entries) in enumerate(groups):
        label, kind = section_kind(sec, entries)
        parts.append("<section>")
        parts.append(
            f"<h2>{html.escape(display_name(sec))} {_badge(label, kind)}</h2>"
        )
        # Section-level failure banner: amber for a max-steps truncation
        # (matching its badge), red for a terminal error. Rendered only on the
        # section's last group — the error ended its final run.
        sec_err = section_errors.get(sec) if last_group_of[sec] == gi else None
        if sec_err:
            status = _normalize_status(outcomes[sec]) if sec in outcomes else ""
            if status == "max_steps":
                cls, mark = "notice", "⚠"
            else:
                cls, mark = "notice error", "✗"
            parts.append(f"<div class='{cls}'>{mark} {html.escape(sec_err)}</div>")
        parts.append("<div class='steps'>")
        parts.append(
            "<div class='head'>#</div><div class='head'>time</div>"
            "<div class='head'>action</div>"
            "<div class='head'>detail</div><div class='head'>screenshot</div>"
        )
        for i, e in enumerate(entries, 1):
            detail = ""
            decision = e.get("decision") or ""
            if decision:
                detail += f"<span class='decision'>{html.escape(decision)}</span>"
            detail += html.escape(_summarize_params(e.get("params") or {}))
            output = e.get("output") or ""
            if output:
                detail += f"<br><span class='out'>{html.escape(output)}</span>"
            # A step explicitly recorded as failed gets a ✗ and a red row —
            # unless it's marked warn (max-steps truncation), which gets ⚠ and
            # amber to match its section badge. The terminal step otherwise
            # gets the completion ✓.
            warned = bool(e.get("warn"))
            failed = e.get("success") is False and not warned
            row_cls = " fail-row" if failed else (" warn-row" if warned else "")
            mark = " ✗" if failed else (" ⚠" if warned else (" ✓" if e.get("finished") else ""))
            action = (e.get("action_type") or "") + mark
            ts = e.get("ts") or 0.0
            clock = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""
            when = clock
            if ts and ts >= offset_base:
                offset = ts - offset_base
                off_txt = _fmt_offset(offset)
                if seekable:
                    when += (
                        f"<br><a href='#' title='Jump the video to this step' "
                        f"onclick='return seekTo({offset:.1f})'>{off_txt}</a>"
                    )
                else:
                    when += f"<br>{off_txt}"
            shot = ""
            thumb = e.get("thumb") or ""
            full = e.get("screenshot") or ""
            if thumb:
                idx = len(shots)
                shots.append({
                    "src": full or thumb,
                    "action": action,
                    "detail": detail,
                    "label": f"{display_name(sec)} · #{i}" + (f" · {clock}" if clock else ""),
                })
                img = f"<img src='{thumb}' loading='lazy'>"
                href = html.escape(full or "#")
                shot = f"<a href='{href}' onclick='return openLb({idx})'>{img}</a>"
            num_div = f"<div class='{row_cls.strip()}'>{i}</div>" if row_cls else f"<div>{i}</div>"
            parts.append(
                num_div
                + f"<div class='when{row_cls}'>{when}</div>"
                f"<div class='act{row_cls}'>{html.escape(action)}</div>"
                f"<div class='detail{row_cls}'>{detail}</div>"
                f"<div class='shot{row_cls}'>{shot}</div>"
            )
        parts.append("</div></section>")

    parts.append(_lightbox(shots))
    parts.append("</body></html>")
    out.write_text("".join(parts), encoding="utf-8")
    return out
