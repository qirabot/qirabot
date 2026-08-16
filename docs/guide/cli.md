---
title: CLI Reference
description: Run natural-language GUI automation tasks from the command line — browser, Android, iOS, and desktop subcommands, recording, reports, and script-friendly exit codes.
---

# CLI Reference

The `qirabot` command runs a task end-to-end without writing Python. It ships
in the core package. `android`, `ios`, and `desktop --window-title/--hwnd`
run on the built-in backends and need no extras. Only `browser`
(`qirabot[browser]`), whole-screen `desktop` (`qirabot[desktop]`), and the
Appium engine (`qirabot[appium]`) need one.

```bash
# Browser (needs qirabot[browser] + `playwright install chromium`)
qirabot browser "Search for SpaceX and get the first sentence of the article" --url wikipedia.org

# Browser — headless/viewport; a persistent profile; or take over a running Chrome via CDP
qirabot browser "..." --headless --viewport 1920x1080
qirabot browser "..." --user-data-dir ~/.qira-profile --channel chrome
qirabot browser "..." --cdp-url http://localhost:9222

# Android — direct over adb (built in; only needs the adb binary, no server)
qirabot android "Open settings and turn on airplane mode"
qirabot android "..." -d emulator-5554 --app-package com.android.settings

# iOS — direct to WebDriverAgent (built in; WDA must be running on :8100)
qirabot ios "Send hi to Alice on WeChat" --bundle-id com.tencent.xin

# Either can go through an Appium server instead (needs qirabot[appium])
qirabot android "..." --appium-url http://localhost:4723
qirabot ios "..." --device "iPhone 15"   # simulators only (selects Appium)

# Desktop via pyautogui (needs qirabot[desktop])
qirabot desktop "Create a new note titled Groceries" --app Notes

# Desktop bound to ONE Windows window (built in) — DirectInput scancode input
qirabot desktop "Open the inventory and list all items" --window-title "Genshin"
qirabot desktop "..." --hwnd 132456

# Mount domain knowledge for the run — game rules, business terms (32KB total)
qirabot browser "Buy 10 stamina potions in the shop" -k game-rules.md -k gm-policy.md

# Environment check — Python, Google Cloud credentials (ADC), backend deps
qirabot doctor

# Model overview — Vertex providers, default models, credential status
qirabot models
```

## Commands

| Command | Purpose |
|---|---|
| `browser INSTRUCTION` | Run an AI task in a local browser ([Browser backend](/backends/browser)) |
| `android INSTRUCTION` | Run an AI task on an Android device ([adb direct](/backends/android), built in; `--appium-url` for Appium) |
| `ios INSTRUCTION` | Run an AI task on an iOS device ([WDA direct](/backends/ios), built in; `--appium-url`/`--device` for Appium) |
| `desktop INSTRUCTION` | Run an AI task on the [desktop screen](/backends/desktop) (pyautogui; `--window-title`/`--hwnd` binds [one Windows window](/backends/windows-games), built in) |
| `install-browser` | One-time Chromium download for the browser backend |
| `open-browser` | Open a browser to log in to websites by hand; the session persists in `--user-data-dir` for later runs |
| `doctor` | Check Python, Google Cloud credentials (ADC + project), and per-backend dependencies |
| `models` | Print the built-in Vertex providers with their default models, the session default model, and whether the configured auth (API key and/or ADC) resolves |
| `skill install [AGENT]` | Copy the bundled [Agent Skill](/guide/agents) into an AI agent's skills directory |
| `skill uninstall [AGENT]` | Remove the skill installed by `skill install` |
| `skill list` | Show known skills directories and the installed skill version |

## Global options

Global options go **before** the subcommand (they configure the Vertex AI
connection):

```bash
qirabot --vertex-project my-gcp-project --vertex-location global browser "..."
```

The project resolves in this order: `--vertex-project` flag >
`QIRA_VERTEX_PROJECT` env var > `GOOGLE_CLOUD_PROJECT` > the ADC
credentials' own project id. The location: `--vertex-location` >
`QIRA_VERTEX_LOCATION` > `GOOGLE_CLOUD_LOCATION` > `global`. Also
available: `--version`.

`--vertex-api-key` (or `QIRA_VERTEX_API_KEY`) authenticates with a
[Vertex AI API key](https://cloud.google.com/vertex-ai/generative-ai/docs/start/api-keys)
instead of ADC, so no gcloud setup is needed. It's a Google Cloud API key,
not an AI Studio key; it only covers `gemini-vertex` models, always uses the
global endpoint, and overrides `--vertex-project`/`--vertex-location`.

`--gemini-api-key` (or `QIRA_GEMINI_API_KEY` / `GEMINI_API_KEY`) is the
[AI Studio API key](https://ai.google.dev/gemini-api/docs/api-key) for the
`gemini` provider, which calls the Gemini Developer API instead of Vertex;
no Google Cloud is involved (`-m gemini/gemini-3.6-flash`).

`--service-tier` (or `QIRA_SERVICE_TIER`) picks the consumption tier —
`flex` for roughly half price on slower, sheddable capacity, `priority` for a
premium on capacity served ahead of standard traffic. Add
`--tier-escalation` to retry one rung up when a tier runs out of capacity
instead of failing the run. Both need the global endpoint and a model that
supports them; see
[Configuration](/advanced/configuration#service-tier).

The model is a task-command option (`-m/--model`, below) and resolves:
`-m` flag > `QIRA_MODEL` env var > the built-in default
`gemini-vertex/gemini-3.6-flash`. A bare provider name selects that
provider's default model (`gemini` → `gemini-3.6-flash`).

## Exit codes

The exit codes are script-friendly: `0` task succeeded, `1` task failed or
any error, `130` interrupted with Ctrl+C. This means
`qirabot browser "..." && next-step` only proceeds on success.

## Machine-readable output

`--output-format json` makes stdout carry exactly one JSON result object (the
human-readable output is suppressed; exit codes are unchanged):

```json
{
  "type": "result",
  "success": true,
  "status": "completed",
  "output": "Signed in and reached the dashboard",
  "task_id": "1a2b3c4d",
  "usage": {
    "ai_steps": 6,
    "input_tokens": 48210,
    "output_tokens": 3120,
    "thinking_tokens": 0,
    "cache_read_tokens": 12040,
    "cache_write_tokens": 0,
    "step_duration_ms": 41830,
    "llm_decision_duration_ms": 28510,
    "total_tokens": 63370
  },
  "report": "qira_runs/2026-08-03/143012-1a2b3c4d/report.html"
}
```

`status` is `completed` / `goal_failed` / `max_steps` / `error` /
`cancelled`: the same values as the SDK's `RunResult.status`, plus
`cancelled` for Ctrl+C and the ESC kill switch. `success` is `true` only for
`completed`. `report` is `null` when reporting is off; the file itself is
written as the process exits, so read it after the CLI returns.

`--output-format stream-json` emits NDJSON: one JSON object per line, flushed
per step, for tools that supervise a run live:

```
{"type": "start", "task_id": "1a2b3c4d", "max_steps": 20}
{"type": "step", "step": 1, "action_type": "click", "params": {"locate": "Login button"}, "decision": "...", ...}
{"type": "step", "step": 2, "action_type": "input", ...}
{"type": "result", "success": true, ...}
```

`step` lines carry the same fields as the SDK's `StepResult` (`step`,
`action_type`, `params`, `decision`, `output`, `finished`, per-step token and
duration counts); the final `result` line is identical to the `json` format's
object. Errors that stop the run, including setup failures such as an
unreachable device, still end with a `result` object (`status: "error"`), so
a consumer always sees one terminal line.

## Shared run options

`browser` / `android` / `ios` / `desktop` all take:

| Option | Default | What it does |
|---|---|---|
| `-n, --name` | derived from the instruction | Run name shown in the HTML report |
| `-m, --model` | `QIRA_MODEL`, else `gemini-vertex/gemini-3.6-flash` | Model as `{provider}/{model}`, provider one of `gemini-vertex` / `gemini` (see [Configuration](/advanced/configuration)) |
| `--thinking-level` | engine default | Thinking override: `minimal` / `low` / `medium` / `high` (see [Configuration](/advanced/configuration#thinking-level)) |
| `--media-resolution` | `QIRA_MEDIA_RESOLUTION`, else `high` | Screenshot detail the model sees: `low` / `medium` / `high` / `ultra_high` (Gemini only); lower it to cut image tokens per step |
| `-l, --language` | instruction's language | Response language: a tag (`zh`, `ja`, `de`, …) or any language name |
| `--max-steps` | `20` | Step budget for the AI task |
| `-k, --knowledge` | — | Knowledge file the AI consults during the task (UTF-8 text; repeatable, 32KB total). Same rules as `bot.ai(knowledge=...)`: files only, no URLs; fetch remote sources yourself first |
| `--report / --no-report` | on | Write an HTML run report |
| `--report-dir` | `./qira_runs/...` | Report output root (env `QIRA_REPORT_DIR`) |
| `--annotate / --no-annotate` | on | Crosshair click/type coordinates on saved screenshots |
| `--overlay / --no-overlay` | on | Always-on-top progress window, plus the hold-ESC kill switch that rides it (macOS/Windows; a silent no-op elsewhere). See [Progress Overlay & Kill Switch](/advanced/overlay) |
| `--record` | off | Record the run to `recording.mp4` (see below) |
| `--output-format` | `text` | `json` / `stream-json` for machine-readable stdout (see [Machine-readable output](#machine-readable-output)) |

## Per-command options

Options for **`browser`** (see the [Browser backend](/backends/browser)):

| Option | Default | What it does |
|---|---|---|
| `-u, --url` | — | URL to open (AI navigates if omitted) |
| `--headless` | off | Headless mode (auto-on when there's no display) |
| `--viewport` | `1280x800` | Viewport as `WIDTHxHEIGHT` |
| `--channel` | bundled Chromium | Use an installed browser: `chrome`, `msedge`, … |
| `--user-data-dir` | — | Persistent profile dir (cookies/logins survive runs) |
| `--browser-arg` | — | Extra Chromium launch arg, repeatable |
| `--cdp-url` | — | Attach to a running Chrome via CDP; mutually exclusive with the four options above |

Options for **`android`** (see the [Android backend](/backends/android)):

| Option | Default | What it does |
|---|---|---|
| `-d, --device` | the only connected device | adb serial from `adb devices` |
| `--app-package` | — | App package to launch (e.g. `com.android.settings`) |
| `--app-activity` | — | App activity to launch |
| `--appium-url` | direct adb, no server | Passing it switches to the [Appium engine](/frameworks/appium) |
| `--record` | off | Record the device screen (adb screenrecord / Appium API) |

Options for **`ios`** (see the [iOS backend](/backends/ios)):

| Option | Default | What it does |
|---|---|---|
| `--wda-url` | `http://127.0.0.1:8100` | WebDriverAgent URL; this selects the device (USB real device: `iproxy 8100 8100`) |
| `--bundle-id` | — | App bundle id to launch (e.g. `com.tencent.xin`) |
| `--device` | — | Simulator device type from `xcrun simctl list devicetypes`; switches to the Appium engine, simulators only (no `-d` short: switching engines is deliberate) |
| `--appium-url` | direct WDA, no server | Appium server URL (with `--device`) |
| `--record` | off | Record the device screen (WDA MJPEG + ffmpeg / Appium API) |
| `--mjpeg-url` | `--wda-url` host on port 9100 | MJPEG stream override for `--record` |

Options for **`desktop`** (see [Desktop](/backends/desktop) and
[Windows & Games](/backends/windows-games)):

| Option | Default | What it does |
|---|---|---|
| `--app` | — | Launch/activate an app first (macOS: name or bundle id; Windows: exe/registered name/UWP id; Linux: executable) |
| `--app-wait` | `2.0` | Seconds to wait for the window after `--app` |
| `--window-title` | — | Bind to the window matching this title regex (Windows window backend) |
| `--hwnd` | — | Bind to a window handle, decimal (Windows window backend) |
| `--ambiguous` | `error` | When several windows match `--window-title`: `error` fails listing them; `largest` picks the biggest matching window |

**`skill install [AGENT]`** installs the bundled
[Agent Skill](/guide/agents) (SKILL.md, preflight script, API reference,
starter templates), version-matched to the installed `qirabot`. `AGENT` is
one of `agents` (the shared `.agents/skills` convention used by Codex,
Cursor, Gemini CLI, and others), `claude`, `codex`, `cursor`; any other tool
is targeted via `--dir PATH`.
`--project` targets the project-level directory under the current directory
instead of the user-level one. Rerun after `uv tool upgrade qirabot` to
upgrade; a directory the command didn't create is never overwritten without
`--force`. For Claude Code the plugin marketplace remains the recommended
install (it auto-updates). `skill uninstall` takes the same target options;
`skill list` shows what's installed where.

`--record` saves `recording.mp4` into the run dir and embeds it in the HTML
report. What gets recorded differs per target:

- `browser` / `desktop` record the host screen via ffmpeg (ffmpeg must be on
  PATH). With a window bound (`--window-title`/`--hwnd`), the recording
  follows that window.
- `android` records the device screen: `adb screenrecord` on the default
  engine, or Appium's recording API on the Appium engine.
- `ios` records the device screen: WDA's MJPEG stream on the default engine
  (needs ffmpeg; a USB real device also needs `iproxy 9100 9100`), or
  Appium's recording API on the Appium engine.

Recording mechanics, report layout, and audio capture are covered in
[Reports & Recording](/advanced/reports). Runs honor the same env vars as
the SDK (`QIRA_REPORT_DIR`, `QIRA_SETTLE_SECONDS`, `QIRA_RECORD*`, and so
on); the full list is in [Configuration](/advanced/configuration).
