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
import math
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, TypeGuard

_CSS = """
:root { color-scheme: light; --bg:#f4f6fa; --card:#fff; --line:#e7eaf0; --text:#111827;
  --muted:#6b7280; --faint:#9ca3af; --model:#4f46e5; --device:#94a3b8; --rate:#f59e0b;
  --outage:#ef4444; --timeout:#a855f7; --pass:#059669; --passbg:#d1fae5; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--text);
  font:14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", Roboto,
  Helvetica, Arial, sans-serif; }
.wrap { max-width:1400px; margin:0 auto; padding:28px 32px 60px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }
.model-bg { background:var(--model); } .device-bg { background:var(--device); }
.rate-bg { background:var(--rate); } .outage-bg { background:var(--outage); }
.timeout-bg { background:var(--timeout); }

/* Header card: title, meta line, then one panel of three zones. */
.hero { position:relative; background:var(--card); border:1px solid var(--line);
  border-radius:16px; padding:22px 26px 20px; box-shadow:0 1px 2px rgba(16,24,40,.04);
  margin-bottom:20px; }
.eyebrow { font-size:11px; letter-spacing:.1em; text-transform:uppercase; color:var(--faint);
  font-weight:700; margin-bottom:6px; }
.hero h1 { font-size:21px; margin:0; letter-spacing:-.01em; line-height:1.4; }
/* Instructions can be paragraphs: titles and task lines show two lines at
   most, and the full text sits behind a toggle (see _instruction_toggle). */
.clamp { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical;
  overflow:hidden; overflow-wrap:anywhere; }
.meta { color:var(--muted); font-size:12.5px; margin-top:4px; display:flex; gap:10px;
  flex-wrap:wrap; }
.meta .sep { color:#d1d5db; } .meta b { color:var(--text); font-weight:500; }
details.instr { margin-top:6px; }
details.instr.auto { display:none; }
details.instr summary { cursor:pointer; font-size:12.5px; color:#4f46e5; font-weight:600;
  list-style:none; display:inline-flex; align-items:center; gap:4px; user-select:none; }
details.instr summary::-webkit-details-marker { display:none; }
details.instr summary::before { content:"▸"; display:inline-block; transition:transform .15s; }
details.instr[open] summary::before { transform:rotate(90deg); }
details.instr pre { margin:8px 0 0; padding:12px 14px; background:#f8fafc;
  border:1px solid var(--line); border-radius:10px; white-space:pre-wrap;
  font:13px/1.6 -apple-system, "PingFang SC", sans-serif; color:#1f2937; max-height:320px;
  overflow:auto; font-weight:400; }
.panel { display:grid; grid-template-columns:220px minmax(0,1.7fr) minmax(0,1fr);
  margin-top:18px; border:1px solid var(--line); border-radius:12px; overflow:hidden; }
.zone { padding:16px 18px; min-width:0; } .zone + .zone { border-left:1px solid var(--line); }
.zl { font-size:11px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em;
  font-weight:600; }
.outcome { background:linear-gradient(180deg,#ecfdf5,#f6fefb); }
.outcome.fail { background:linear-gradient(180deg,#fef2f2,#fffafa); }
.outcome.warn { background:linear-gradient(180deg,#fffbeb,#fffdf5); }
.outcome.neutral { background:#fafbfc; }
.oc { display:flex; align-items:center; gap:10px; margin-top:8px; }
.oc .ic { width:34px; height:34px; border-radius:50%; background:var(--pass); color:#fff;
  font-weight:800; display:flex; align-items:center; justify-content:center; font-size:18px;
  flex:none; }
.fail .oc .ic { background:#dc2626; } .warn .oc .ic { background:#d97706; }
.neutral .oc .ic { background:#9ca3af; }
.oc .t { font-size:22px; font-weight:750; color:#065f46; }
.fail .oc .t { color:#991b1b; } .warn .oc .t { color:#92400e; } .neutral .oc .t { color:#374151; }
.os { font-size:12.5px; color:var(--muted); margin-top:8px; }
.os b { color:var(--text); font-weight:600; }
.tn { display:flex; align-items:baseline; gap:12px; margin-top:4px; flex-wrap:wrap; }
.tn .v { font-size:28px; font-weight:700; letter-spacing:-.02em; }
.tn .lost { font-size:12.5px; color:#b45309; font-weight:600; }
.bar { display:flex; height:10px; border-radius:5px; overflow:hidden; background:#eef0f4;
  margin-top:10px; }
.bar > span { display:block; height:100%; }
.groups { display:flex; margin-top:8px; font-size:12px; color:var(--muted); gap:16px;
  flex-wrap:wrap; }
.grp { display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
.grp .gt { font-size:10.5px; text-transform:uppercase; letter-spacing:.05em; color:var(--faint);
  font-weight:600; }
.grp.lostg { padding-left:14px; border-left:2px solid #fde68a; }
.grp i { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:5px; }
.grp b { color:var(--text); font-weight:600; margin-left:3px; }
.grp .z, .grp .z b { color:#d1d5db; font-weight:500; }
.kv { display:grid; grid-template-columns:58px minmax(0,1fr); gap:7px 10px; margin:8px 0 0;
  font-size:12.5px; }
.kv dt { color:var(--muted); } .kv dd { margin:0; min-width:0; overflow-wrap:anywhere; }
.kv .main { font-weight:600; }
.kv .note { display:block; color:#b45309; font-size:11.5px; }
.kv .note.plain { color:var(--faint); }

/* Token popover: opened from the Tokens value, printed inline. */
.tokbtn { font:inherit; font-weight:650; color:#4338ca; background:#eef2ff;
  border:1px solid #c7d2fe; border-radius:8px; padding:1px 8px; cursor:pointer; }
.tokbtn:hover { background:#e0e7ff; }
.tokbtn .car { font-size:10px; display:inline-block; transition:transform .15s; }
.tokbtn[aria-expanded='true'] .car { transform:rotate(180deg); }
.tokpop { display:none; position:absolute; right:26px; z-index:20;
  width:min(860px, calc(100% - 52px)); filter:drop-shadow(0 12px 32px rgba(16,24,40,.18)); }
.tokpop.open { display:block; }
.tokpop::before { content:""; position:absolute; top:-7px; right:var(--arrow, 60px);
  width:12px; height:12px; background:#fafbfc; border-left:1px solid var(--line);
  border-top:1px solid var(--line); transform:rotate(45deg); }
.tok { border:1px solid var(--line); border-radius:12px; overflow:hidden; background:#fff; }
.tok .cap { display:flex; align-items:center; padding:10px 16px; background:#fafbfc;
  border-bottom:1px solid var(--line); }
.tokx { margin-left:auto; border:0; background:none; cursor:pointer; color:var(--faint);
  font-size:14px; }
.tokx:hover { color:var(--text); }
.tok table { font-size:12.5px; min-width:0; }
.tok th { background:#fff; font-size:10.5px; padding:8px 14px; }
.tok td { padding:8px 14px; }
.tok tfoot td { background:#fafbfc; font-weight:650; border-top:1px solid var(--line);
  border-bottom:0; }
.tok .foot { padding:8px 16px; font-size:11.5px; color:var(--faint);
  border-top:1px solid var(--line); }
.dim { color:#d1d5db; }
.tier { display:inline-block; font-size:10.5px; font-weight:700; padding:1px 7px;
  border-radius:6px; text-transform:uppercase; letter-spacing:.03em; background:#f3f4f6;
  color:#4b5563; }
.tier.flex { background:#eef2ff; color:#4338ca; }
.tier.priority { background:#fae8ff; color:#86198f; }

/* Task overview (two or more ai() tasks). */
.tasks { margin-top:16px; border:1px solid var(--line); border-radius:12px; overflow:hidden; }
.tasks a { display:grid; grid-template-columns:26px 104px minmax(0,1fr) 76px 70px 120px;
  gap:12px; align-items:center; padding:10px 16px; text-decoration:none; color:inherit;
  font-size:13px; }
.tasks a + a { border-top:1px solid #f1f3f6; }
.tasks a:hover { background:#fafbff; }
.tasks .i { color:var(--faint); font-weight:600; }
.tasks .n { white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
.tasks .r { text-align:right; color:var(--muted); font-size:12.5px; white-space:nowrap; }
.mb { display:flex; height:6px; border-radius:3px; overflow:hidden; background:#eef0f4; }
.mb span { display:block; }

.badge { display:inline-block; padding:2px 10px; border-radius:999px; font-size:11.5px;
  font-weight:700; white-space:nowrap; text-align:center; }
.pass { background:var(--passbg); color:var(--pass); }
.fail { background:#fee2e2; color:#b91c1c; }
.warn { background:#fef3c7; color:#92400e; }
.neutral { background:#eef0f3; color:#4b5563; }
.notice { background:#fff7e0; color:#7a5200; padding:10px 20px; font-size:13px;
  border-bottom:1px solid #fde68a; }
.notice.error { background:#fef2f2; color:#991b1b; border-bottom-color:#fecaca; }
.wrap > .notice { border:1px solid #fde68a; border-radius:10px; margin-bottom:18px; }
/* Recording. .media wraps the player and the step sections; _PLAYER_JS tags
   it portrait / landscape from the video's own size once metadata loads and
   sets --ar (width/height), so the box and its marker strip match the
   picture exactly. Landscape (and the default): capped and centered above
   the steps. Portrait on a wide screen: a sticky column beside the steps. */
.player { margin-bottom:18px; }
.pbox { --ar:1.778; width:min(100%, 880px, calc(55vh * var(--ar))); margin:0 auto; }
.pbox video { display:block; width:100%; aspect-ratio:var(--ar); background:#0b0d12;
  border-radius:12px; }
.ticks { position:relative; height:14px; margin:8px 2px 0; cursor:pointer; visibility:hidden; }
.ticks.ready { visibility:visible; }
.ticks::before { content:""; position:absolute; left:0; right:0; top:6px; height:2px;
  border-radius:1px; background:#e2e5eb; }
.tkfill { position:absolute; left:0; top:6px; height:2px; width:0; border-radius:1px;
  background:#4f46e5; pointer-events:none; }
.tk { position:absolute; top:2px; width:4px; height:10px; margin-left:-2px; padding:0; border:0;
  border-radius:2px; background:#a5b4fc; cursor:pointer; }
.tk:hover, .tk.now { top:0; height:14px; background:#4f46e5; }
.tk.warn { background:#f59e0b; } .tk.fail { background:#ef4444; }
.pcap { display:flex; gap:8px; margin-top:6px; font-size:11.5px; color:var(--muted); }
.pcap b { color:var(--text); font-weight:600; }
.pcap .miss { color:#b45309; font-weight:600; }
.pcap .now { margin-left:auto; min-width:0; overflow:hidden; text-overflow:ellipsis;
  white-space:nowrap; }
.steps tr.now td { background:#eef2ff; }
.steps tr.now td.idx { box-shadow:inset 3px 0 #4f46e5; color:#4f46e5; }
@media (min-width: 1240px) {
  .media.portrait { display:grid; grid-template-columns:minmax(0,1fr) 300px; gap:18px;
    align-items:start; }
  .media.portrait > .main { grid-column:1; grid-row:1; min-width:0; }
  .media.portrait > .player { grid-column:2; grid-row:1; position:sticky; top:20px; margin:0; }
  .media.portrait .pbox { width:min(100%, calc((100vh - 96px) * var(--ar))); }
  .media.portrait .steps { min-width:840px; }
}

/* Step sections. */
section { background:var(--card); border:1px solid var(--line); border-radius:16px;
  overflow:hidden; box-shadow:0 1px 2px rgba(16,24,40,.04); margin-bottom:18px; }
section > h2 { margin:0; padding:14px 20px; font-size:15px; border-bottom:1px solid var(--line); }
section > h2 .row { display:flex; align-items:center; gap:10px; }
section > h2 .sub { margin-left:auto; color:var(--muted); font-weight:400; font-size:12.5px;
  white-space:nowrap; }
section > h2 .ins { margin-top:6px; font-weight:400; font-size:12.5px; color:var(--muted); }
.scroll { overflow-x:auto; }
table { width:100%; border-collapse:collapse; }
th { text-align:left; font-size:11px; font-weight:600; color:var(--muted);
  text-transform:uppercase; letter-spacing:.04em; padding:10px 12px; background:#fafbfc;
  border-bottom:1px solid var(--line); white-space:nowrap; }
th.num, td.num { text-align:right; }
td { padding:12px; border-bottom:1px solid #f1f3f6; vertical-align:top; }
.steps { min-width:980px; }
.steps tr:hover td { background:#fafbff; }
.steps tr.hot td { background:#fffbeb; }
.steps tr.warn-row td { background:#fffaef; }
.steps tr.fail-row td { background:#fff5f5; }
.idx { color:var(--faint); font-weight:600; width:32px; }
.when { font-size:11.5px; color:var(--muted); white-space:nowrap; width:1%; }
.when span { display:block; color:var(--faint); }
.when a { color:#4f46e5; text-decoration:none; }
.when a:hover { text-decoration:underline; }
/* Custom tool names (e.g. capture_lqa_frame) can exceed the column;
   underscores aren't break points, so allow breaking anywhere. */
.act { font-weight:650; min-width:112px; max-width:200px; overflow-wrap:break-word; }
.act .done { color:var(--pass); }
.warn-row .act { color:#92400e; } .fail-row .act { color:#b91c1c; }
.dec { color:var(--muted); font-size:12.5px; margin-bottom:4px; }
.dec.clip { display:-webkit-box; -webkit-line-clamp:4; -webkit-box-orient:vertical;
  overflow:hidden; overflow-wrap:anywhere; }
.dec.clip.open { display:block; -webkit-line-clamp:unset; }
.more { border:0; background:none; padding:0; margin:-2px 0 6px; cursor:pointer;
  font:inherit; font-size:12px; font-weight:600; color:#4f46e5; }
.more:hover { text-decoration:underline; }
.det { font-size:12.5px; white-space:pre-wrap; word-break:break-word; color:#1f2937; }
.out { margin-top:6px; padding:6px 10px; background:#f6f8fb; border-radius:8px; font-size:12px;
  white-space:pre-wrap; word-break:break-word; color:#374151; border-left:3px solid #c7d2fe; }
.out.md { white-space:normal; font-size:12.5px; line-height:1.55; }
.out.md p { margin:0 0 6px; } .out.md > :last-child { margin-bottom:0; }
.out.md ul, .out.md ol { margin:2px 0 6px; padding-left:20px; } .out.md li { margin:1px 0; }
.out.md li > ul, .out.md li > ol { margin:1px 0 2px; }
.out.md strong { color:#111827; font-weight:650; }
.out.md code { font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:11.5px;
  background:#e8ecf3; padding:0 4px; border-radius:4px; }
.mdl { font-size:12px; white-space:nowrap; width:1%; }
.mdl.same { color:var(--faint); } .mdl.diff { color:#b45309; font-weight:600; }
.mdl .tier { margin-top:3px; }
.dur { font-weight:650; white-space:nowrap; font-size:13px; }
.minibar { display:flex; height:4px; border-radius:2px; overflow:hidden; background:#eef0f4;
  margin:6px 0 0 auto; width:84px; }
.minibar span { display:block; height:100%; }
.waits { white-space:nowrap; width:140px; text-align:right; }
.wchip { display:block; font-size:11.5px; font-weight:650; padding:1px 8px; border-radius:6px;
  margin-bottom:3px; text-align:right; }
.wchip.rate { background:#fef3c7; color:#92400e; }
.wchip.outage { background:#fee2e2; color:#991b1b; }
.wchip.timeout { background:#f3e8ff; color:#6b21a8; }
.none { color:#d1d5db; }
.shot { width:176px; }
.shot img { width:160px; border-radius:8px; border:1px solid var(--line); display:block;
  transition:transform .08s ease; }
.shot a { display:block; cursor:zoom-in; }
.shot a:hover img { transform:scale(1.02); }
.shot .reused { display:block; margin-top:4px; color:var(--faint); font-size:10.5px;
  font-style:italic; }

/* Lightbox: the frame on the left, everything about the step in one fixed
   panel on the right. Both keep their size from step to step, so paging
   never makes anything jump. */
.lightbox { position:fixed; inset:0; z-index:1000; display:none;
  background:rgba(10,12,16,.94); -webkit-backdrop-filter:blur(6px); backdrop-filter:blur(6px); }
.lightbox.open { display:flex; }
.lb-stage { flex:1; min-width:0; position:relative; display:flex; align-items:center;
  justify-content:center; padding:28px 72px; }
.lb-stage img { max-width:100%; max-height:100%; object-fit:contain; border-radius:10px;
  box-shadow:0 20px 60px rgba(0,0,0,.55); background:#fff; }
.lb-nav { position:absolute; top:50%; transform:translateY(-50%); width:44px; height:44px;
  border-radius:50%; border:0; cursor:pointer; display:flex; align-items:center;
  justify-content:center; font-size:24px; line-height:1; color:#fff;
  background:rgba(255,255,255,.1); transition:background .12s; user-select:none;
  -webkit-user-select:none; }
.lb-nav:hover { background:rgba(255,255,255,.22); }
.lb-prev { left:16px; } .lb-next { right:16px; }
.lb-side { width:400px; flex:none; display:flex; flex-direction:column; background:var(--card);
  color:var(--text); box-shadow:-12px 0 40px rgba(0,0,0,.25); }
.lb-head { display:flex; align-items:center; gap:10px; padding:14px 18px;
  border-bottom:1px solid var(--line); }
.lb-where { font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--faint);
  font-weight:700; }
.lb-count { margin-left:auto; font-size:12px; color:var(--muted);
  font-variant-numeric:tabular-nums; }
.lb-close { border:0; background:none; cursor:pointer; font-size:16px; color:var(--muted);
  width:28px; height:28px; border-radius:8px; }
.lb-close:hover { background:#f1f3f7; color:var(--text); }
.lb-body { flex:1; overflow:auto; padding:16px 18px 20px; }
.lb-act { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:17px;
  font-weight:700; }
.lb-act .done { color:var(--pass); } .lb-act.fail { color:#b91c1c; } .lb-act.warn { color:#92400e; }
.lb-facts { display:flex; flex-wrap:wrap; gap:6px; margin:8px 0 14px; }
.lb-facts span { font-size:11.5px; color:#4b5563; background:#f3f4f6; border-radius:6px;
  padding:2px 8px; white-space:nowrap; }
.lb-facts .wchip { display:inline-block; margin:0; }
.lb-facts .reused { background:#fff7ed; color:#9a3412; }
.lb-detail .dec { font-size:13.5px; color:var(--text); line-height:1.6; margin-bottom:10px; }
.lb-detail .det { font-size:12px; color:#374151; background:#f8fafc; border:1px solid var(--line);
  border-radius:8px; padding:6px 10px; }
.lb-detail .out { margin-top:10px; }
.lb-foot { padding:10px 18px; border-top:1px solid var(--line); font-size:11.5px;
  color:var(--faint); display:flex; gap:14px; }
.lb-foot a { color:#4f46e5; text-decoration:none; margin-left:auto; }
.lb-foot a:hover { text-decoration:underline; }
kbd { font-family:inherit; font-size:10.5px; border:1px solid var(--line); border-bottom-width:2px;
  border-radius:4px; padding:0 4px; color:var(--muted); background:#fff; }

@media (max-width: 900px) {
  .lightbox.open { flex-direction:column; }
  .lb-stage { padding:16px 56px; }
  .lb-side { width:auto; height:38vh; box-shadow:0 -12px 40px rgba(0,0,0,.25); }
  .wrap { padding:12px; }
  .panel { grid-template-columns:1fr; }
  .zone + .zone { border-left:0; border-top:1px solid var(--line); }
  .tasks a { grid-template-columns:22px 92px minmax(0,1fr) 64px 60px; }
  .tasks a > :last-child { display:none; }
}
@media print {
  body { background:#fff; }
  .tokpop { display:block; position:static; width:auto; margin-top:16px; filter:none; }
  .tokpop::before, .tokx, .tokbtn .car { display:none; }
  details.instr pre { max-height:none; }
  .dec.clip { display:block; -webkit-line-clamp:unset; }
  .more { display:none; }
}
"""


_COORD_KEYS = ("x", "y", "start_x", "start_y", "end_x", "end_y")


def _is_coord(value: Any) -> TypeGuard[float]:
    """A finite real number. bool is excluded (True would render as 1)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


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

    # Coordinates, formatted compactly. Only real numbers, and a key counts
    # as rendered only once it is: a custom tool's own `x` can be any type,
    # or come without a `y`, and must then fall through to key=value below.
    x, y = params.get("x"), params.get("y")
    if _is_coord(x) and _is_coord(y):
        parts.append(f"({int(x)}, {int(y)})")
        rendered.update(_COORD_KEYS[:2])
    sx, sy, ex, ey = (params.get(k) for k in _COORD_KEYS[2:])
    if _is_coord(sx) and _is_coord(sy) and _is_coord(ex) and _is_coord(ey):
        parts.append(f"({int(sx)},{int(sy)})→({int(ex)},{int(ey)})")
        rendered.update(_COORD_KEYS[2:])

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


# Statuses that mean a budget ran out before the model finished: amber, and
# tallied as "truncated" rather than failed.
_TRUNCATED = frozenset({"max_steps", "max_duration"})

# RunStatus -> (badge label, css class): one entry per qirabot.client.RunStatus
# value — that Literal is the single status vocabulary, and a test keeps this
# mapping in lockstep with it. Unknown statuses fall back to FAIL — a status
# we don't recognize should read as broken, never silently green.
_STATUS_KINDS = {
    "completed": ("PASS", "pass"),
    "goal_failed": ("FAIL", "fail"),
    "max_steps": ("MAX STEPS", "warn"),
    "max_duration": ("MAX DURATION", "warn"),
    "error": ("ERROR", "fail"),
    # A deliberate user abort (ESC hold / mouse-to-corner / Ctrl+C via
    # cancel()): amber, not red — the bot didn't fail, the user stopped it.
    "cancelled": ("CANCELLED", "warn"),
}


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


_LIGHTBOX_JS = """
const SHOTS = __SHOTS__;
let lbIdx = 0;
const lb = document.getElementById('lightbox');
const $ = function (id) { return document.getElementById(id); };
function renderLb() {
  const s = SHOTS[lbIdx];
  if (!s) return;
  $('lb-img').src = s.src;
  $('lb-img').alt = s.where + ' · ' + s.action;
  $('lb-where').textContent = s.where;
  $('lb-count').textContent = (lbIdx + 1) + ' / ' + SHOTS.length;
  $('lb-act').innerHTML = s.act;
  $('lb-act').className = 'lb-act ' + s.status;
  $('lb-facts').innerHTML = s.facts;
  $('lb-detail').innerHTML = s.detail;
  $('lb-full').href = s.src;
  $('lb-body').scrollTop = 0;
  // Warm the neighbours so paging swaps instantly.
  [lbIdx - 1, lbIdx + 1].forEach(function (i) {
    const n = SHOTS[(i + SHOTS.length) % SHOTS.length];
    if (n) (new Image()).src = n.src;
  });
}
function openLb(i) {
  lbIdx = i; renderLb(); lb.classList.add('open');
  document.body.style.overflow = 'hidden';
  return false;
}
function closeLb() { lb.classList.remove('open'); document.body.style.overflow = ''; }
function stepLb(d) {
  if (!SHOTS.length) return;
  lbIdx = (lbIdx + d + SHOTS.length) % SHOTS.length;
  renderLb();
}
$('lb-stage').addEventListener('click', function (e) {
  if (e.target.id === 'lb-stage') closeLb();
});
$('lb-close').addEventListener('click', closeLb);
$('lb-prev').addEventListener('click', function (e) { e.stopPropagation(); stepLb(-1); });
$('lb-next').addEventListener('click', function (e) { e.stopPropagation(); stepLb(1); });
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
        "<div class='lb-stage' id='lb-stage'>"
        "<button class='lb-nav lb-prev' id='lb-prev' title='Previous (←)'>‹</button>"
        "<img id='lb-img' alt=''>"
        "<button class='lb-nav lb-next' id='lb-next' title='Next (→)'>›</button>"
        "</div>"
        "<aside class='lb-side'>"
        "<div class='lb-head'><span class='lb-where' id='lb-where'></span>"
        "<span class='lb-count' id='lb-count'></span>"
        "<button class='lb-close' id='lb-close' title='Close (Esc)'>✕</button></div>"
        "<div class='lb-body' id='lb-body'><div class='lb-act' id='lb-act'></div>"
        "<div class='lb-facts' id='lb-facts'></div>"
        "<div class='lb-detail' id='lb-detail'></div></div>"
        "<div class='lb-foot'><span><kbd>←</kbd> <kbd>→</kbd> step</span>"
        "<span><kbd>Esc</kbd> close</span>"
        "<a id='lb-full' href='#' target='_blank' rel='noopener'>Open full size ↗</a></div>"
        "</aside></div>"
        f"<script>{script}</script>"
    )


_PAGE_JS = """
(function () {
  // A single-line instruction gets its "Full instruction" toggle only when
  // the two-line clamp actually cut it — that depends on the viewport.
  document.querySelectorAll('details.instr.auto').forEach(function (d) {
    var c = d.previousElementSibling;
    if (c && c.scrollHeight > c.clientHeight + 1) d.classList.remove('auto');
  });
  // Long step decisions: same idea, a four-line clamp whose toggle appears
  // only where it cut. (Rows inside a collapsed layout measure 0 — skip.)
  document.querySelectorAll('.dec.clip').forEach(function (c) {
    var b = c.nextElementSibling;
    if (!b || !b.classList.contains('more')) return;
    if (c.scrollHeight > c.clientHeight + 1) b.hidden = false;
    b.addEventListener('click', function () {
      var open = c.classList.toggle('open');
      b.textContent = open ? 'Show less' : 'Show more';
    });
  });
  // Token popover, anchored under the Tokens button.
  var btn = document.getElementById('tokbtn'), pop = document.getElementById('tokpop');
  if (!btn || !pop) return;
  var hero = pop.parentElement;
  function place() {
    var h = hero.getBoundingClientRect(), b = btn.getBoundingClientRect();
    pop.style.top = (b.bottom - h.top + 10) + 'px';
    var right = h.right - 26 - (b.left + b.width / 2);
    pop.style.setProperty('--arrow', Math.max(12, right - 6) + 'px');
  }
  function set(open) {
    pop.classList.toggle('open', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) place();
  }
  btn.addEventListener('click', function (e) {
    e.stopPropagation(); set(!pop.classList.contains('open'));
  });
  document.getElementById('tokx').addEventListener('click', function () { set(false); });
  pop.addEventListener('click', function (e) { e.stopPropagation(); });
  document.addEventListener('click', function () { set(false); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') set(false); });
  window.addEventListener('resize', function () { if (pop.classList.contains('open')) place(); });
})();
"""

# Only emitted with a recording. Rows that can seek carry data-t (their
# offset into the video, seconds) and data-l (a label for the caption).
_PLAYER_JS = """
(function () {
  var media = document.querySelector('.media'), v = media && media.querySelector('video');
  if (!v) return;
  var player = media.querySelector('.player'), box = player.querySelector('.pbox');
  var bar = player.querySelector('.ticks'), fill = player.querySelector('.tkfill');
  var cap = player.querySelector('.pcap .now');
  var rows = [].slice.call(media.querySelectorAll('tr[data-t]')).map(function (r) {
    return { el: r, t: parseFloat(r.dataset.t), l: r.dataset.l || '' };
  }).sort(function (a, b) { return a.t - b.t; });
  var ticks = [], cur = -1, built = false;
  function sidebar() { return getComputedStyle(player).position === 'sticky'; }
  function visible(el) {
    var r = el.getBoundingClientRect();
    return r.top >= 0 && r.bottom <= window.innerHeight;
  }
  function layout() {
    if (!v.videoWidth || !v.videoHeight) return;
    box.style.setProperty('--ar', (v.videoWidth / v.videoHeight).toFixed(4));
    var portrait = v.videoHeight > v.videoWidth;
    media.classList.toggle('portrait', portrait);
    media.classList.toggle('landscape', !portrait);
    var d = v.duration;
    if (!bar || built || !isFinite(d) || d <= 0) return;
    built = true;
    rows.forEach(function (r, i) {
      if (r.t > d) return;
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'tk' + (r.el.classList.contains('fail-row') ? ' fail'
        : r.el.classList.contains('warn-row') ? ' warn' : '');
      b.style.left = (r.t / d * 100) + '%';
      b.title = r.l;
      b.addEventListener('click', function (e) { e.stopPropagation(); seek(r.t, false); });
      bar.appendChild(b);
      ticks[i] = b;
    });
    bar.classList.add('ready');
    // Steps past the end (a recorder that fell behind, or stopped early)
    // get no marker; say so rather than let the strip look complete.
    var miss = rows.filter(function (r) { return r.t > d + 1; }).length;
    if (miss) {
      var s = document.createElement('span');
      s.className = 'miss';
      s.textContent = miss + (miss === 1 ? ' step' : ' steps') + ' after the recording ends';
      cap.parentNode.insertBefore(s, cap);
    }
  }
  function sync() {
    var t = v.currentTime, d = v.duration;
    if (fill && isFinite(d) && d > 0) fill.style.width = Math.min(100, t / d * 100) + '%';
    var i = -1;
    while (i + 1 < rows.length && rows[i + 1].t <= t + 0.05) i++;
    if (i === cur) return;
    if (cur >= 0) {
      rows[cur].el.classList.remove('now');
      if (ticks[cur]) ticks[cur].classList.remove('now');
    }
    cur = i;
    if (i < 0) { cap.textContent = ''; return; }
    rows[i].el.classList.add('now');
    if (ticks[i]) ticks[i].classList.add('now');
    cap.textContent = rows[i].l;
    // Follow along only beside the steps: in the stacked layout the video
    // sits above them, so scrolling to a row would hide the video.
    if (!v.paused && sidebar() && !visible(rows[i].el)) {
      rows[i].el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }
  function seek(t, reveal) {
    v.currentTime = t;
    if (reveal && !visible(v)) v.scrollIntoView({ behavior: 'smooth', block: 'center' });
    var p = v.play();
    if (p && p.catch) p.catch(function () {});
  }
  window.seekTo = function (t) { seek(t, true); return false; };
  if (bar) bar.addEventListener('click', function (e) {
    var r = bar.getBoundingClientRect(), d = v.duration;
    if (isFinite(d) && d > 0) seek(Math.max(0, (e.clientX - r.left) / r.width) * d, false);
  });
  v.addEventListener('loadedmetadata', layout);
  v.addEventListener('durationchange', layout);
  v.addEventListener('timeupdate', sync);
  v.addEventListener('seeked', sync);
  if (v.readyState >= 1) layout();
})();
"""

# Provider-ledger buckets in display order: (entry/stats key, css class, label).
_WAITS = (
    ("rate_limit_wait_ms", "rate", "Rate limit"),
    ("outage_wait_ms", "outage", "Outage"),
    ("timeout_wait_ms", "timeout", "Timeout"),
)

# RunStatus -> the Outcome zone's headline for a single judged task.
_OUTCOME_WORDS = {
    "completed": "Passed",
    "goal_failed": "Failed",
    "max_steps": "Max steps",
    "max_duration": "Max duration",
    "error": "Error",
    "cancelled": "Cancelled",
}

_DEFAULT_TIER = "standard"


def _esc(value: Any) -> str:
    return html.escape(str(value))


_MD_ITEM = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$")
_MD_HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_MD_CODE = re.compile(r"`([^`\n]+)`")
# ** only: __x__ would mangle identifiers such as __init__.
_MD_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")


def _md_inline(text: str) -> str:
    """Escape, then bold and inline code. Code spans are cut out first so
    their contents stay literal."""
    out, last = [], 0
    for m in _MD_CODE.finditer(text):
        out.append(_MD_BOLD.sub(lambda b: f"<strong>{b[1]}</strong>", _esc(text[last:m.start()])))
        out.append(f"<code>{_esc(m[1])}</code>")
        last = m.end()
    out.append(_MD_BOLD.sub(lambda b: f"<strong>{b[1]}</strong>", _esc(text[last:])))
    return "".join(out)


def _render_markdown(text: str) -> str:
    """The small Markdown subset models answer in: paragraphs, headings (as
    bold lines), ordered and unordered lists (nested by indent), bold and
    inline code. Everything is escaped first; links, images and raw HTML
    stay literal text, so model output can never inject markup."""
    parts: list[str] = []
    para: list[str] = []
    # Open lists, innermost last: (indent, tag).
    stack: list[tuple[int, str]] = []

    def flush_para() -> None:
        if para:
            parts.append(f"<p>{'<br>'.join(para)}</p>")
            para.clear()

    def close_to(indent: int) -> None:
        while stack and stack[-1][0] > indent:
            parts.append(f"</li></{stack.pop()[1]}>")

    for raw in text.strip().splitlines():
        line = raw.rstrip()
        item = _MD_ITEM.match(line)
        if item:
            flush_para()
            indent = len(item[1].expandtabs(4))
            tag = "ul" if item[2] in "-*+" else "ol"
            close_to(indent)
            if stack and stack[-1][0] == indent and stack[-1][1] == tag:
                parts.append("</li><li>")
            else:
                if stack and stack[-1][0] == indent:  # same depth, other list kind
                    parts.append(f"</li></{stack.pop()[1]}>")
                start = int(item[2][:-1]) if tag == "ol" else 1
                attr = f" start='{start}'" if start != 1 else ""
                parts.append(f"<{tag}{attr}><li>")
                stack.append((indent, tag))
            parts.append(_md_inline(item[3]))
            continue
        if not line.strip():
            flush_para()
            continue
        if stack and raw[:1].isspace():
            # An indented line under a list item continues that item.
            parts.append(f"<br>{_md_inline(line.strip())}")
            continue
        close_to(-1)
        heading = _MD_HEADING.match(line)
        if heading:
            flush_para()
            parts.append(f"<p><strong>{_md_inline(heading[1])}</strong></p>")
        else:
            para.append(_md_inline(line.strip()))
    flush_para()
    close_to(-1)
    return "".join(parts)


def _output_html(text: str) -> str:
    """A step's output box. JSON-looking output keeps its layout verbatim;
    anything else renders as Markdown, which is what models answer in."""
    stripped = text.strip()
    if stripped[:1] in "{[":
        return f"<div class='out mono'>{_esc(text)}</div>"
    return f"<div class='out md'>{_render_markdown(text)}</div>"


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in text.splitlines() if ln.strip()), text.strip())


def _split_label(label: str) -> tuple[str, str]:
    """``"flex — some calls served as priority, …"`` -> (main, note): the
    backend's model/tier labels put the configured value first and any
    deviation after an em dash."""
    main, _, note = label.partition(" — ")
    return main.strip(), note.strip()


def _instruction_toggle(text: str) -> str:
    """The full instruction behind a toggle, under a clamped line.

    Multi-line text always gets it (the clamp shows the first line only);
    a single line only when the browser actually clamped it, which _PAGE_JS
    decides — so a line that fits shows no toggle at all.
    """
    lines = text.strip().count("\n") + 1
    size = f"{lines} lines, {len(text)} chars" if lines > 1 else f"{len(text)} chars"
    auto = "" if lines > 1 else " auto"
    return (
        f"<details class='instr{auto}'><summary>Full instruction · {size}</summary>"
        f"<pre>{_esc(text.strip())}</pre></details>"
    )


def _segments(t: Mapping[str, int], duration: int) -> list[tuple[str, str, int]]:
    """(css class, label, ms) for model, device and the three waits.

    Device is whatever the step's wall clock spent outside the model and
    the waits: screenshot, action, settle, custom tools, local overhead.
    """
    model = int(t.get("model_ms", 0) or 0)
    waits = [(css, label, int(t.get(key, 0) or 0)) for key, css, label in _WAITS]
    device = max(0, duration - model - sum(ms for _, _, ms in waits))
    return [("model", "Model", model), ("device", "Device", device), *waits]


def _bar(segments: list[tuple[str, str, int]], cls: str) -> str:
    total = sum(ms for _, _, ms in segments)
    if not total:
        return ""
    spans = "".join(
        f"<span class='{css}-bg' style='width:{ms / total * 100:.2f}%' "
        f"title='{label} {fmt_ms(ms)}'></span>"
        for css, label, ms in segments
        if ms
    )
    return f"<div class='{cls}'>{spans}</div>"


def _has_ledger(t: Mapping[str, int]) -> bool:
    return any(t.get(k) for k in ("model_ms", *(key for key, _, _ in _WAITS)))


def _time_zone(stats: Mapping[str, int]) -> str:
    duration = int(stats.get("step_duration_ms", 0) or 0)
    if not duration:
        return ""
    head = f"<div class='tn'><span class='v'>{fmt_ms(duration)}</span>"
    if not _has_ledger(stats):
        # An injected provider keeps no ledger: the wall clock is all there
        # is, and a bar of pure "device" time would misread.
        return f"<div class='zone'><div class='zl'>Time</div>{head}</div></div>"
    segs = _segments(stats, duration)
    lost = sum(ms for _, _, ms in segs[2:])
    if lost:
        head += (
            f"<span class='lost'>{fmt_ms(lost)} lost to capacity "
            f"({lost / max(duration, 1) * 100:.0f}%)</span>"
        )

    def item(css: str, label: str, ms: int) -> str:
        zero = "" if ms else " class='z'"
        return f"<span{zero}><i class='{css}-bg'></i>{label}<b>{fmt_ms(ms) if ms else '0'}</b></span>"

    working = "".join(item(*s) for s in segs[:2])
    lost_items = "".join(item(*s) for s in segs[2:])
    return (
        f"<div class='zone'><div class='zl'>Time</div>{head}</div>{_bar(segs, 'bar')}"
        f"<div class='groups'><div class='grp'><span class='gt'>Working</span>{working}</div>"
        f"<div class='grp lostg'><span class='gt'>Lost</span>{lost_items}</div></div></div>"
    )


def _token_total(stats: Mapping[str, int]) -> int:
    # input_tokens is the non-cached prompt portion only; the cached portion
    # rides in the cache counters, so the total needs both. Thinking is
    # already counted within output tokens — do not add it again.
    return (
        int(stats.get("input_tokens", 0) or 0)
        + int(stats.get("cache_read_tokens", 0) or 0)
        + int(stats.get("cache_write_tokens", 0) or 0)
        + int(stats.get("output_tokens", 0) or 0)
    )


def _tok(n: int) -> str:
    return fmt_tokens(n) if n else "<span class='dim'>—</span>"


def _token_table(tokens_by: Mapping[tuple[str, str], Mapping[str, int]]) -> str:
    """Spend per model × tier — the rate each row is billed at.

    Response excludes thinking here (the providers fold thinking into the
    output count), so the columns add up to Total without double counting.
    """
    rows = sorted(
        tokens_by.items(),
        key=lambda kv: -(kv[1].get("input", 0) + kv[1].get("cached", 0) + kv[1].get("output", 0)),
    )
    body = []
    tot = dict.fromkeys(("calls", "input", "cached", "response", "thinking"), 0)
    for (model, tier), r in rows:
        think = int(r.get("thinking", 0))
        vals = {
            "calls": int(r.get("calls", 0)),
            "input": int(r.get("input", 0)),
            "cached": int(r.get("cached", 0)),
            "response": max(0, int(r.get("output", 0)) - think),
            "thinking": think,
        }
        for k, v in vals.items():
            tot[k] += v
        tier_cell = (
            f"<span class='tier {_esc(tier)}'>{_esc(tier)}</span>" if tier
            else "<span class='dim'>—</span>"
        )
        body.append(
            f"<tr><td class='mono'>{_esc(model or 'unknown')}</td><td>{tier_cell}</td>"
            f"<td class='num mono'>{vals['calls']}</td>"
            + "".join(
                f"<td class='num mono'>{_tok(vals[k])}</td>"
                for k in ("input", "cached", "response", "thinking")
            )
            + f"<td class='num mono'>{_tok(sum(vals[k] for k in vals if k != 'calls'))}</td></tr>"
        )
    foot = (
        f"<tr><td colspan='2'>Total</td><td class='num mono'>{tot['calls']}</td>"
        + "".join(
            f"<td class='num mono'>{_tok(tot[k])}</td>"
            for k in ("input", "cached", "response", "thinking")
        )
        + f"<td class='num mono'>{_tok(sum(tot[k] for k in tot if k != 'calls'))}</td></tr>"
    )
    return (
        "<div class='tok'><div class='cap'><span class='zl'>Token usage</span>"
        "<button class='tokx' id='tokx' title='Close (Esc)'>✕</button></div>"
        "<table><thead><tr><th>Model</th><th>Tier</th><th class='num'>AI calls</th>"
        "<th class='num'>Prompt</th><th class='num'>Cached</th><th class='num'>Response</th>"
        "<th class='num'>Thinking</th><th class='num'>Total</th></tr></thead>"
        f"<tbody>{''.join(body)}</tbody><tfoot>{foot}</tfoot></table>"
        "<div class='foot'>Every AI call is counted, failed ones and single-step calls "
        "(click / verify / extract) included.</div></div>"
    )


def _model_zone(
    stats: Mapping[str, int],
    model: str,
    tier: str,
    tokens_by: Mapping[tuple[str, str], Mapping[str, int]],
) -> str:
    rows = []
    if model:
        main, note = _split_label(model)
        rows.append(("Model", f"<span class='main mono'>{_esc(main)}</span>", note))
    if tier:
        main, note = _split_label(tier)
        rows.append(("Tier", f"<span class='main'>{_esc(main)}</span>", note))
    total = _token_total(stats)
    if total:
        if tokens_by:
            value = (
                "<button class='tokbtn' id='tokbtn' aria-expanded='false' "
                "aria-controls='tokpop' title='Token usage by model and tier'>"
                f"{fmt_tokens(total)} <span class='car'>▾</span></button>"
            )
        else:
            value = f"<span class='main'>{fmt_tokens(total)}</span>"
        rows.append(("Tokens", value, ""))
    if not rows:
        return ""
    cells = "".join(
        f"<dt>{label}</dt><dd>{value}"
        # Deviations (fallback, another tier) are amber; "default" is not one.
        + (
            f"<span class='note{' plain' if note == 'default' else ''}'>{_esc(note)}</span>"
            if note
            else ""
        )
        + "</dd>"
        for label, value, note in rows
    )
    return f"<div class='zone'><div class='zl'>Model &amp; tokens</div><dl class='kv'>{cells}</dl></div>"


def _outcome_zone(statuses: list[str], stats: Mapping[str, int]) -> str:
    total_steps = int(stats.get("total_steps", 0) or 0)
    ai = int(stats.get("ai_steps", 0) or 0)
    if total_steps:
        detail = "all AI" if ai == total_steps else f"{ai} AI"
        steps = f"<b>{total_steps}</b> step{'s' if total_steps != 1 else ''}, {detail}"
    else:
        steps = ""
    if not statuses:
        return (
            "<div class='zone outcome neutral'><div class='zl'>Outcome</div>"
            "<div class='oc'><span class='ic'>•</span><span class='t'>No AI tasks</span></div>"
            f"<div class='os'>{steps}</div></div>"
        )
    passed = sum(1 for st in statuses if st == "completed")
    truncated = sum(1 for st in statuses if st in _TRUNCATED)
    failed = sum(1 for st in statuses if _STATUS_KINDS.get(st, ("", "fail"))[1] == "fail")
    if passed == len(statuses):
        kind, icon = "pass", "✓"
    elif failed:
        kind, icon = "fail", "✗"
    else:
        kind, icon = "warn", "!"
    if len(statuses) == 1:
        word = _OUTCOME_WORDS.get(statuses[0], "Failed")
    elif kind == "pass":
        word = "All passed"
    elif failed:
        word = f"{failed} failed"
    else:
        word = f"{len(statuses) - passed} not passed"
    tally = f"<b>{passed}</b>/{len(statuses)} passed"
    if truncated:
        tally += f" · {truncated} truncated"
    sep = " · " if steps else ""
    return (
        # The pass look is the zone's default: a bare "pass" class would pick
        # up the badge colours instead.
        f"<div class='zone outcome{'' if kind == 'pass' else ' ' + kind}'><div class='zl'>Outcome</div>"
        f"<div class='oc'><span class='ic'>{icon}</span><span class='t'>{word}</span></div>"
        f"<div class='os tally'>{tally}{sep}{steps}</div></div>"
    )


def _waits_cell(e: Mapping[str, Any]) -> str:
    chips = [
        f"<span class='wchip {css} mono'>{label.lower()} {fmt_ms(int(e[key]))}</span>"
        for key, css, label in _WAITS
        if e.get(key)
    ]
    none = "<span class='none'>—</span>"
    return f"<td class='waits'>{''.join(chips) or none}</td>"


def _duration_cell(e: Mapping[str, Any]) -> str:
    if "duration_ms" not in e:
        return "<td class='num'><span class='none'>—</span></td>"
    duration = int(e.get("duration_ms") or 0)
    bar = _bar(_segments(e, duration), "minibar") if _has_ledger(e) and duration else ""
    return f"<td class='num'><div class='dur mono'>{fmt_ms(duration)}</div>{bar}</td>"


def _model_cell(e: Mapping[str, Any], default_model: str, default_tier: str) -> str:
    name = str(e.get("model") or "")
    if not name:
        return "<td class='mdl'><span class='none'>—</span></td>"
    same = not default_model or name == default_model
    served = str(e.get("tier") or "")
    pill = (
        f"<br><span class='tier {_esc(served)}'>{_esc(served)}</span>"
        if served and served != default_tier
        else ""
    )
    return f"<td class='mdl mono {'same' if same else 'diff'}'>{_esc(name)}{pill}</td>"


def write_html(
    log: list[dict[str, Any]],
    path: str | Path,
    *,
    title: str = "",
    task_id: str = "",
    outcomes: Mapping[str, str] | None = None,
    section_errors: Mapping[str, str] | None = None,
    recording: str = "",
    recording_start: float = 0.0,
    record_error: str = "",
    stats: dict[str, int] | None = None,
    model: str = "",
    tier: str = "",
    instructions: Mapping[str, str] | None = None,
    section_stats: Mapping[str, Mapping[str, int]] | None = None,
    tokens_by: Mapping[tuple[str, str], Mapping[str, int]] | None = None,
    default_model: str = "",
    device: tuple[str, int, int] | None = None,
) -> Path:
    """Render ``log`` to a self-contained HTML report at ``path``.

    ``section_errors`` maps a section to its failure text (e.g. a terminal
    server error), rendered as a banner above that section's step table —
    these are section-level outcomes, not steps. The client records none for
    a max-steps truncation (the badge says it all); text a caller passes for
    a ``max_steps`` section still renders, amber rather than red.

    ``recording_start`` is the epoch time the embedded recording began; when
    set (and a recording is present), each step's elapsed offset becomes a
    link that seeks the video to that moment.

    The title is ``title`` when given (an explicit task name), else the
    first line of the only task's instruction, else "N tasks".
    ``instructions`` maps a section key to its full instruction (a repeat
    run's key carries a ``#2`` suffix); without it the key stands in.

    ``section_stats`` (section -> wall clock and ledger slice) feeds the
    task overview, ``tokens_by`` ((model, tier) -> counts) the token
    popover, and ``default_model`` lets step rows mute the run's primary
    model and highlight any other. AI entries may carry ``duration_ms``,
    ``model_ms``, the three ``*_wait_ms`` keys, ``model`` and ``tier``;
    entries without them (manual steps, older logs) show dashes.
    ``device`` is the (platform, width, height) shown in the meta line.
    """
    outcomes = outcomes or {}
    section_errors = section_errors or {}
    instructions = instructions or {}
    section_stats = section_stats or {}
    tokens_by = tokens_by or {}
    stats = stats or {}
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

    # ai() tasks in run order, each once ("setup" holds the manual steps).
    tasks: list[str] = list(dict.fromkeys(name for name, _ in groups if name != "setup"))
    task_no = {name: i for i, name in enumerate(tasks, 1)}
    multi = len(tasks) > 1 or (len(tasks) == 1 and any(n == "setup" for n, _ in groups))

    def instruction(name: str) -> str:
        return instructions.get(name) or name

    def kind_of(name: str) -> tuple[str, str]:
        if name in outcomes:
            return _STATUS_KINDS.get(outcomes[name], ("FAIL", "fail"))
        return ("", "neutral")

    # -- header -----------------------------------------------------------
    if title:
        h1 = f"<h1 class='clamp'>{_esc(title)}</h1>"
        page_title = title
        if len(tasks) == 1:
            h1 += _instruction_toggle(instruction(tasks[0])).replace(" auto'", "'")
    elif len(tasks) == 1:
        text = instruction(tasks[0])
        page_title = _first_line(text)
        h1 = f"<h1 class='clamp' title='{_esc(text)}'>{_esc(page_title)}</h1>"
        h1 += _instruction_toggle(text)
    elif tasks:
        page_title = f"{len(tasks)} tasks"
        h1 = f"<h1>{page_title}</h1>"
    else:
        page_title = "Manual steps"
        h1 = f"<h1>{page_title}</h1>"

    # Header timestamp: the run's first stamped step, so a report regenerated
    # from an old log still shows when the run happened. Falls back to "now"
    # for logs predating the "ts" field.
    first_ts = next((e["ts"] for e in log if e.get("ts")), 0.0)
    when = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(first_ts) if first_ts else time.localtime(),
    )
    meta = [f"<span>{when}</span>"]
    if device:
        platform, width, height = device
        meta.append(f"<span>{_esc(platform)} · {width}×{height}</span>")
    if task_id:
        meta.append(f"<span>Run <b class='mono'>{_esc(task_id)}</b></span>")

    # Each judged section counts once, even when its steps split across
    # several timeline groups (outcomes are keyed per section).
    judged = [name for name in tasks if name in outcomes]
    statuses = [outcomes[name] for name in judged]
    zones = (
        _outcome_zone(statuses, stats)
        + _time_zone(stats)
        + _model_zone(stats, model, tier, tokens_by)
    )

    parts: list[str] = []
    parts.append("<!doctype html><html><head><meta charset='utf-8'>")
    parts.append("<meta name='viewport' content='width=device-width, initial-scale=1'>")
    parts.append(f"<title>{_esc(page_title)} — Qirabot report</title>")
    parts.append(f"<style>{_CSS}</style></head><body><div class='wrap'>")
    parts.append("<div class='hero'><div class='eyebrow'>Qirabot run report</div>")
    parts.append(h1)
    sep = "<span class='sep'>·</span>"
    parts.append(f"<div class='meta'>{sep.join(meta)}</div>")
    parts.append(f"<div class='panel'>{zones}</div>")

    # Task overview: one row per ai() task once there are two or more.
    if len(tasks) > 1:
        rows = []
        for name in tasks:
            label, kind = kind_of(name)
            badge = _badge(label, kind) if label else _badge("—", "neutral")
            n_steps = sum(len(es) for g, es in groups if g == name)
            ss = section_stats.get(name, {})
            duration = int(ss.get("step_duration_ms", 0) or 0)
            bar = _bar(_segments(ss, duration), "mb") if duration and _has_ledger(ss) else "<span></span>"
            text = instruction(name)
            rows.append(
                f"<a href='#task-{task_no[name]}'><span class='i'>{task_no[name]}</span>{badge}"
                f"<span class='n' title='{_esc(text)}'>{_esc(_first_line(text))}</span>"
                f"<span class='r'>{n_steps} step{'s' if n_steps != 1 else ''}</span>"
                f"<span class='r mono'>{fmt_ms(duration) if duration else ''}</span>{bar}</a>"
            )
        parts.append(f"<div class='tasks'>{''.join(rows)}</div>")
    if tokens_by and _token_total(stats):
        parts.append(
            "<div class='tokpop' id='tokpop' role='dialog' aria-label='Token usage'>"
            f"{_token_table(tokens_by)}</div>"
        )
    parts.append("</div>")  # .hero

    # A step's offset into the video, as a link that seeks it, only when
    # the video is seekable. Without one, an offset from the run start would
    # only restate the clock time beside it. Entries from runs predating the
    # "ts" field just leave the time cell empty.
    seekable = bool(recording) and recording_start > 0
    default_tier = _split_label(tier)[0] or _DEFAULT_TIER

    if recording:
        # The player and the step sections share .media so a portrait
        # recording can become a sticky column beside the steps (_PLAYER_JS
        # picks the layout from the video's real size). The step-marker
        # strip needs step offsets, so it exists only when seekable.
        ticks = "<div class='ticks'><div class='tkfill'></div></div>" if seekable else ""
        parts.append(
            "<div class='media'><div class='player'><div class='pbox'>"
            f"<video controls preload='metadata' src='{_esc(recording)}'></video>{ticks}"
            "<div class='pcap'><b>Recording</b><span class='now'></span></div>"
            "</div></div><div class='main'>"
        )
    elif record_error:
        parts.append(f"<div class='notice'>⚠ {_esc(record_error)}</div>")

    # Sections. Every step with a thumbnail also becomes a lightbox "shot" so the
    # viewer can page across all screenshots regardless of which section they're in.
    shots: list[dict[str, str]] = []
    for gi, (sec, entries) in enumerate(groups):
        is_last = last_group_of[sec] == gi
        steps_label = f"{len(entries)} step{'s' if len(entries) != 1 else ''}"
        sub = steps_label
        if sec == "setup":
            heading = "Manual steps"
            badge = _badge(steps_label, "neutral")
            ins = ""
        else:
            heading = f"Task {task_no[sec]}" if multi else "Steps"
            label, kind = kind_of(sec)
            badge = _badge(label, kind) if label else _badge(steps_label, "neutral")
            ins = ""
            if multi:
                text = instruction(sec)
                ins = (
                    f"<div class='ins clamp' title='{_esc(text)}'>{_esc(_first_line(text))}</div>"
                    + _instruction_toggle(text)
                )
        group_ms = sum(int(e.get("duration_ms") or 0) for e in entries)
        if group_ms:
            sub += f" · {fmt_ms(group_ms)}"
        anchor = f" id='task-{task_no[sec]}'" if sec != "setup" and is_last else ""
        parts.append(f"<section{anchor}>")
        parts.append(
            f"<h2><div class='row'>{_esc(heading)} {badge}<span class='sub'>{sub}</span></div>"
            f"{ins}</h2>"
        )
        # Section-level failure banner: amber for a max-steps truncation
        # (matching its badge), red for a terminal error. Rendered only on the
        # section's last group — the error ended its final run.
        sec_err = section_errors.get(sec) if is_last else None
        if sec_err:
            status = outcomes.get(sec, "")
            if status in _TRUNCATED:
                cls, mark = "notice", "⚠"
            else:
                cls, mark = "notice error", "✗"
            parts.append(f"<div class='{cls}'>{mark} {_esc(sec_err)}</div>")
        if not entries:
            parts.append("</section>")
            continue
        parts.append(
            "<div class='scroll'><table class='steps'><thead><tr><th>#</th><th>Time</th>"
            "<th>Action</th><th>Detail</th><th>Model</th><th class='num'>Duration</th>"
            "<th class='num'>Waits</th><th>Screenshot</th></tr></thead><tbody>"
        )
        for i, e in enumerate(entries, 1):
            detail = ""
            # The table clamps a long decision (models sometimes think aloud
            # in it for a page); the lightbox renders the unclamped `detail`.
            # _PAGE_JS unhides the toggle only where the clamp actually cut.
            row_detail = ""
            decision = e.get("decision") or ""
            if decision:
                detail += f"<div class='dec'>{_esc(decision)}</div>"
                row_detail += (
                    f"<div class='dec clip'>{_esc(decision)}</div>"
                    "<button type='button' class='more' hidden>Show more</button>"
                )
            params = dict(e.get("params") or {})
            output = e.get("output") or ""
            if e.get("action_type") == "done":
                # The done tool's success flag is the row's ✓ / ✗ and the task
                # badge; its result is the output box below, shown once.
                params.pop("success", None)
                if str(params.get("result", "")).strip() == output.strip():
                    params.pop("result", None)
            summary = _summarize_params(params)
            rest = ""
            if summary:
                rest += f"<div class='det mono'>{_esc(summary)}</div>"
            if output:
                rest += _output_html(output)
            detail += rest
            row_detail += rest
            # A step explicitly recorded as failed gets a ✗ and a red row —
            # unless it's marked warn (max-steps truncation), which gets ⚠ and
            # amber to match its section badge. The terminal step otherwise
            # gets the completion ✓.
            warned = bool(e.get("warn"))
            failed = e.get("success") is False and not warned
            hot = any(e.get(key) for key, _, _ in _WAITS)
            row_cls = "fail-row" if failed else ("warn-row" if warned else ("hot" if hot else ""))
            mark = " ✗" if failed else (" ⚠" if warned else (" ✓" if e.get("finished") else ""))
            action = (e.get("action_type") or "") + mark
            act_html = _esc(e.get("action_type") or "")
            if mark == " ✓":
                act_html += " <span class='done'>✓</span>"
            elif mark:
                act_html += _esc(mark)
            ts = e.get("ts") or 0.0
            clock = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""
            when_cell = clock
            if seekable and ts and ts >= recording_start:
                offset = ts - recording_start
                when_cell += (
                    f"<span><a href='#' title='Jump the video to this step' "
                    f"onclick='return seekTo({offset:.1f})'>{_fmt_offset(offset)}</a></span>"
                )
            shot = ""
            thumb = e.get("thumb") or ""
            full = e.get("screenshot") or ""
            # The step decided on a frame captured for an earlier step (the
            # device hadn't moved since). Same picture, but it predates the
            # step — say so rather than let it read as freshly captured.
            reused = bool(e.get("reused_frame"))
            if thumb:
                idx = len(shots)
                facts = [f"<span>{clock}</span>"] if clock else []
                if "duration_ms" in e:
                    facts.append(f"<span>{fmt_ms(int(e.get('duration_ms') or 0))}</span>")
                if e.get("model"):
                    model_fact = _esc(e["model"])
                    if e.get("tier"):
                        model_fact += f" · {_esc(e['tier'])}"
                    facts.append(f"<span>{model_fact}</span>")
                facts += [
                    f"<span class='wchip {css}'>{label.lower()} {fmt_ms(int(e[key]))}</span>"
                    for key, css, label in _WAITS
                    if e.get(key)
                ]
                if reused:
                    facts.append("<span class='reused'>frame from an earlier step</span>")
                shots.append({
                    "src": full or thumb,
                    "where": f"{heading} · step {i}",
                    "action": action,
                    "act": act_html,
                    "status": "fail" if failed else ("warn" if warned else ""),
                    "facts": "".join(facts),
                    "detail": detail,
                })
                img = f"<img src='{thumb}' loading='lazy'>"
                href = _esc(full or "#")
                shot = f"<a href='{href}' onclick='return openLb({idx})'>{img}</a>"
                if reused:
                    shot += "<span class='reused'>frame from an earlier step</span>"
            cls_attr = f" class='{row_cls}'" if row_cls else ""
            if seekable and ts and ts >= recording_start:
                label = f"{heading} · {i} {e.get('action_type') or ''}".rstrip()
                cls_attr += f" data-t='{ts - recording_start:.1f}' data-l='{_esc(label)}'"
            parts.append(
                f"<tr{cls_attr}><td class='idx'>{i}</td>"
                f"<td class='when mono'>{when_cell}</td>"
                f"<td class='act mono'>{act_html}</td>"
                f"<td>{row_detail}</td>"
                + _model_cell(e, default_model, default_tier)
                + _duration_cell(e)
                + _waits_cell(e)
                + f"<td class='shot'>{shot}</td></tr>"
            )
        parts.append("</tbody></table></div></section>")

    if recording:
        parts.append("</div></div>")  # .main, .media
    parts.append("</div>")  # .wrap
    parts.append(_lightbox(shots))
    parts.append(f"<script>{_PAGE_JS}</script>")
    if recording:
        parts.append(f"<script>{_PLAYER_JS}</script>")
    parts.append("</body></html>")
    out.write_text("".join(parts), encoding="utf-8")
    return out
