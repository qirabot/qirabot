# Qirabot CLI reference (condensed)

The `qirabot` command ships with the package (any install/extra). Same auth as
the SDK: Google Cloud ADC — set `GOOGLE_APPLICATION_CREDENTIALS` to a
service-account JSON or run `gcloud auth application-default login` once; for
gemini-vertex models a Vertex AI API key works instead (`--vertex-api-key` or
`QIRA_VERTEX_API_KEY` — a Google Cloud key, not an AI Studio key). The
`gemini` provider (`-m gemini/<model>`) calls the Gemini Developer API with
an AI Studio key (`--gemini-api-key`, `QIRA_GEMINI_API_KEY` or
`GEMINI_API_KEY`) — no Google Cloud involved. The
CLI also loads `./.env` automatically (the SDK doesn't), so `QIRA_MODEL` etc.
can live there. One run command = one `ai()` run — there is no CLI equivalent
of `extract`/`verify`/`wait_for` or of chaining several `ai()` calls in one
session; those need the SDK.

## Global options — go BEFORE the subcommand

```bash
qirabot --vertex-project my-proj browser "..."   # right
qirabot browser "..." --vertex-project my-proj   # wrong (unknown option)
```

| Option | Default | Notes |
|---|---|---|
| `--vertex-project` | env `QIRA_VERTEX_PROJECT`, else `GOOGLE_CLOUD_PROJECT`, else the ADC credentials' own project | Google Cloud project for the Vertex providers |
| `--vertex-location` | env `QIRA_VERTEX_LOCATION`, else `GOOGLE_CLOUD_LOCATION`, else `global` | Vertex location |

`-h/--help` works everywhere and prints each option's default; `--version`
prints the package version.

## Run commands: `browser` / `android` / `ios` / `desktop`

All four take the instruction as the positional argument and share:

| Option | Default | Notes |
|---|---|---|
| `-n/--name` | derived from the instruction (first line, ≤60 chars) | run name shown in the report |
| `-m/--model` | env `QIRA_MODEL`, else the built-in default | `"{provider}/{model}"`, e.g. `gemini-vertex/gemini-3.6-flash` — providers listed by `qirabot models` |
| `--thinking-level` | model's setting | thinking override: `minimal`/`low`/`medium`/`high` |
| `--media-resolution` | env `QIRA_MEDIA_RESOLUTION`, else `high` | screenshot detail the model sees: `low`/`medium`/`high`/`ultra_high` (Gemini only) |
| `-l/--language` | engine default | e.g. `zh`, `en` |
| `--max-steps` | `20` | AI step budget |
| `--report/--no-report` | report | HTML run report |
| `--report-dir` | env `QIRA_REPORT_DIR`, else `./qira_runs/<date>/<run>/` | output root |
| `--annotate/--no-annotate` | annotate | crosshair on saved screenshots |
| `--record` | off | see per-command semantics below — host screen on browser/desktop, **device** screen on android/ios |
| `--output-format` | `text` | `json` = stdout carries one final JSON result object; `stream-json` = NDJSON, a `start` line + one line per step + the result object |

**Exit codes** (CI-gateable): `0` = model achieved the goal · `1` = failed /
error / max-steps exhausted · `130` = Ctrl+C (run recorded as *cancelled* in
the report, not failed). Live per-step trace prints to stdout while running
(`[3/20] click "Login" └ reasoning…`), final line is `Done: <output>` or
`Failed: <output>`.

**Machine-readable output** — with `--output-format json`/`stream-json`,
stdout carries only JSON (rich output is suppressed; exit codes unchanged).
Every exit path ends with one result object:
`{"type":"result","success":bool,"status":"completed|goal_failed|max_steps|error|cancelled","output":str,"task_id":str,"usage":{"ai_steps","input_tokens","output_tokens","thinking_tokens","cache_read_tokens","cache_write_tokens","step_duration_ms","total_tokens",…},"report":path|null}`.
`report` is the `report.html` path, written when the process exits — read it
after the CLI returns. `stream-json` additionally prints, one JSON object per
line: `{"type":"start","task_id",…,"max_steps":…}` first, then a
`{"type":"step",…}` line per AI step whose fields mirror the SDK's
`StepResult` (`step`, `action_type`, `params`, `decision`, `output`,
`finished`, per-step tokens/duration).

### `qirabot browser "<instruction>"`

| Option | Notes |
|---|---|
| `-u/--url` | start URL (scheme optional). Omit and the AI navigates itself — then name the site in the instruction. |
| `--headless` | headless Chromium. On display-less Linux a headed launch auto-falls-back to headless anyway. |
| `--viewport` | `1280x800` |
| `--channel` | `chrome` / `msedge` — use the installed browser instead of bundled Chromium |
| `--user-data-dir` | persistent profile → login survives across runs (pass an absolute path) |
| `--browser-arg` | extra Chromium arg, repeatable |
| `--cdp-url` | attach to a running Chrome (`http://localhost:9222` or a Browserless/Browserbase `wss://`). Mutually exclusive with `--headless/--user-data-dir/--channel/--browser-arg`. |

`--record` = host screen via ffmpeg (needs ffmpeg on PATH).

### `qirabot android "<instruction>"`

Default drives the device straight over adb — built in, zero Python
dependencies, no server. Passing `--appium-url` switches to the Appium engine:

```bash
qirabot android "Open settings"                    # the only adb device
qirabot android "..." -d emulator-5554             # pick one of several
qirabot android "..." --app-package com.android.settings --app-activity .Settings
qirabot android "..." --appium-url http://localhost:4723 -d emulator-5554   # via Appium
```

| Option | Notes |
|---|---|
| `-d/--device` | adb serial; optional with exactly one device. Appium engine: passed as `deviceName`. |
| `--app-package` / `--app-activity` | app to launch first |
| `--appium-url` | passing this flag selects the Appium engine (needs a running server) |

`--record` = **device** screen on both engines (direct: adb screenrecord;
Appium: Appium's recording API).

### `qirabot ios "<instruction>"`

Default talks to WebDriverAgent directly (built in, zero extra installs) — WDA
must already be running (USB real device: `iproxy 8100 8100` first). Passing
`--appium-url` or `--device` (simulator) switches to the Appium engine, for
simulators or auto WDA build/sign:

```bash
qirabot ios "..." --bundle-id com.tencent.xin           # WDA on 127.0.0.1:8100
qirabot ios "..." --wda-url http://192.168.1.20:8100    # another device's WDA
qirabot ios "..." --device "iPhone 15" --bundle-id com.apple.Preferences   # simulator via Appium
```

| Option | Notes |
|---|---|
| `--wda-url` | `http://127.0.0.1:8100` — how the direct engine picks the device. **Direct engine only.** |
| `--bundle-id` | app to launch (via WDA `app_launch`, iOS 17+-safe) |
| `--device` | a simulator device type from `xcrun simctl list devicetypes` — passing it selects the Appium engine (no `-d` short) |
| `--appium-url` | passing this flag selects the Appium engine |
| `--mjpeg-url` | WDA MJPEG stream for `--record` (default: `--wda-url` host on port 9100). **Direct engine + `--record` only.** |

`--record` = **device** screen. The direct engine transcodes WDA's MJPEG stream
(needs ffmpeg; USB real device also needs `iproxy 9100 9100` — probed up front,
fails fast with the fix). The Appium engine uses Appium's recording API, no
extra setup. Engine-mismatched flags are hard usage errors, not ignored.

### `qirabot desktop "<instruction>"`

Whole primary screen via pyautogui (any OS); `--window-title`/`--hwnd` binds to
one Windows window instead (built in — game-readable scancode input,
window-relative screenshots).

| Option | Notes |
|---|---|
| `--window-title` | regex over visible window titles — selects the Windows window backend. **Windows only.** |
| `--hwnd` | explicit window handle — selects the Windows window backend. **Windows only.** |
| `--app` | launch/activate an app first. macOS: app name or bundle id; Windows: exe path / registered name / UWP AppUserModelID; Linux: executable |
| `--app-wait` | `2.0` — seconds to wait for the window after `--app` |

`--record` = host screen via ffmpeg.

## Utility commands (useful on the SDK path too)

| Command | What it does |
|---|---|
| `qirabot install-browser` | One-time Chromium download for the browser backend (wraps `playwright install chromium`; required form in isolated `uv tool` installs, where playwright's own CLI is not on PATH). |
| `qirabot doctor` | Environment check: Python, Google Cloud credentials (ADC + project), each backend's deps, ffmpeg. Exit `0` when at least one backend can run end-to-end — gate setup scripts/CI on it. |
| `qirabot models` | List the built-in Vertex providers (`claude-vertex` / `gemini-vertex`), their default models, the session default, and whether ADC credentials resolve — the valid `-m`/`QIRA_MODEL` values. |

## When the CLI is the wrong tool → use the SDK

- The script must **branch or read values**: no `extract`/`verify`/`wait_for`.
- **Several `ai()` calls / mixed primitives** in one session: the CLI is one
  instruction per invocation, and device state does not survive across runs.
- **Custom targets**: your own Selenium driver / an already-built Appium
  session — `bind()` is SDK-only.
