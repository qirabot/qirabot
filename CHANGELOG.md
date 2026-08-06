# Changelog

## 3.1.1 (2026-08-06)

### Language: any language, better adherence, new default

- `language` now accepts any value: common tags (`zh`, `ja`, `ko`, `de`,
  `fr`, …) map to the native name of the language in the prompt; anything
  else — a rarer tag or a plain language name like `廣東話` — is passed to
  the model as written. Previously everything except `zh` silently fell
  back to English.
- **Behavior change:** with `language` unset, responses now follow the
  language the instruction is written in, instead of defaulting to English.
  Pass `language="en"` to keep the old behavior.
- The language requirement is now also stated on each tool's `reason`
  field, so per-step reasoning drifts to the wrong language far less often
  on tasks whose content is in another language.

## 3.1.0 (2026-08-06)

### Breaking: claude-vertex removed

The `claude-vertex` provider is gone; `gemini-vertex` and `gemini` are the
two remaining providers. A `model="claude-vertex/..."` value now fails at
construction with the standard unknown-provider hint. Pin `qirabot<3.1` if
you still need Claude on Vertex.

### Vertex AI API key authentication (gemini-vertex)

`gemini-vertex` models can now authenticate with a Vertex AI API key instead
of ADC — no gcloud setup needed. Pass `Qirabot(vertex_api_key=...)`, set
`QIRA_VERTEX_API_KEY`, or use the CLI's global `--vertex-api-key`.

- This is a Google Cloud API key (created in the Cloud console or Vertex AI
  express mode), **not** an AI Studio key. `GOOGLE_API_KEY` is deliberately
  not read, since it commonly holds an AI Studio key that Vertex rejects.
- API-key auth is project-bound server-side and only the global endpoint
  accepts it: requests go to `aiplatform.googleapis.com` with the short
  `publishers/google/...` path, and `vertex_project` / `vertex_location`
  are ignored (logged) when a key is configured.
- A configured key always wins over project/location settings.
- `qirabot doctor` / `qirabot models` report key mode; with a Vertex API
  key configured, ADC is not needed for anything.

### New provider: `gemini` — Gemini Developer API with AI Studio keys

`model="gemini/{model}"` (e.g. `gemini/gemini-3.6-flash`) calls the Gemini
Developer API (`generativelanguage.googleapis.com`) instead of Vertex AI — no
Google Cloud project, no ADC. Auth is an AI Studio API key:
`Qirabot(gemini_api_key=...)`, `QIRA_GEMINI_API_KEY`, `GEMINI_API_KEY` (the
official variable), or the CLI's global `--gemini-api-key`.

- Same wire format and engine behavior as `gemini-vertex` (shared request
  builder/parser); only host, path and auth differ.
- The provider requires a key — there is no credential fallback — and the
  missing-key error names the exact knobs.

## 3.0.0 (2026-08-03)

### Breaking: the decision engine now runs locally

The v2 cloud decision service is gone. In v3 the engine runs inside the SDK
process: each screenshot is sent directly from your machine to a vision model
you configure on Google Vertex AI, authenticated with your own Google Cloud
credentials (Application Default Credentials). No Qirabot account, no API key,
no per-step billing, and no Qirabot server in the loop.

**Breaking changes**

- Authentication is Google Cloud ADC: set `GOOGLE_APPLICATION_CREDENTIALS` to
  a service-account JSON, run `gcloud auth application-default login`, or run
  on GCE (metadata server). The Qirabot account/API-key flow is removed.
- Model selection is explicit and local:
  `Qirabot(model="{provider}/{model}")` or `QIRA_MODEL`, with provider one of
  `claude-vertex` / `gemini-vertex`. Default:
  `gemini-vertex/gemini-3.6-flash`.
- Google Cloud project/region: `vertex_project=` / `vertex_location=`
  constructor parameters, `--vertex-project` / `--vertex-location` CLI global
  options, or `QIRA_VERTEX_PROJECT` / `QIRA_VERTEX_LOCATION` (falling back to
  `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`, then to the project
  carried by the ADC credentials; location defaults to `global`).
- Constructing `Qirabot()` with `QIRA_API_KEY` set and no model configured
  raises an error with migration guidance instead of silently ignoring the
  stale variable.
- `task_id` is now a local run id of the form `local-<hex>`; nothing is
  synced to a server. `fail()` / `cancel()` remain, recording the run's
  terminal outcome locally for the HTML report.
- New required dependency: `google-auth`.

**Removed → v3 equivalent**

| v2 | v3 |
| --- | --- |
| `Qirabot(api_key=...)` | Removed — Google Cloud ADC (`GOOGLE_APPLICATION_CREDENTIALS` or `gcloud auth application-default login`) |
| `Qirabot(base_url=...)` | Removed — no server endpoint; the SDK calls Vertex AI directly |
| `Qirabot(model_alias=...)` | `Qirabot(model="{provider}/{model}")` or `QIRA_MODEL` |
| `Qirabot(task_id=...)` | Removed — run ids are generated locally (`local-<hex>`) |
| `Qirabot(source=...)` | Removed — no server-side task records |
| `Qirabot(heartbeat=...)` | Removed — no server connection to keep alive |
| `Qirabot(sync_local_steps=...)` | Removed — steps are only recorded locally (HTML report) |
| `QIRA_API_KEY` env | Removed — ADC (see above) |
| `QIRA_BASE_URL` env | Removed |
| — | New: `vertex_project=` / `vertex_location=` params; `QIRA_VERTEX_PROJECT` / `QIRA_VERTEX_LOCATION` env |
| `qirabot login` | Removed — `gcloud auth application-default login` (or a service-account JSON) |
| `qirabot task` | Removed — no server-side task list; run details are in the local HTML report |
| `qirabot screenshot` | Removed |
| `qirabot models` (cloud alias list) | `qirabot models` — lists the Vertex providers, their default models, and checks ADC |
| `qirabot doctor` (API-key / server check) | `qirabot doctor` — checks Python, Google Cloud credentials (ADC + project), and backend dependencies |
| CLI globals `--api-key` / `--base-url` / `--timeout` / `--verify-ssl` | Removed — new globals: `--vertex-project` / `--vertex-location` |
| `-m/--model` (cloud model alias) | `-m/--model` — local model string `{provider}/{model}` |

**Added**

- Vertex AI providers: `claude-vertex`, `gemini-vertex`.
- `QIRA_LOCATE_FORMAT` (optional; `bbox_yx_1000`) — element-location output
  format override.
- `QIRA_ENGINE_TRACE=<dir>` — debugging: appends one JSONL record per engine
  step and saves the step screenshots into the directory.
- CLI: `--output-format text|json|stream-json` on `browser` / `android` /
  `ios` / `desktop`. `json` prints one final JSON result object on stdout
  (`success`, `status`, `output`, `task_id`, `usage` token/step totals, and
  the `report.html` path); `stream-json` prints NDJSON — a `start` line, one
  line per AI step mirroring the SDK's `StepResult` fields, then the same
  result object. Every exit path (done / failed / error / cancelled, and
  setup failures such as an unreachable device) ends with a result object;
  exit codes are unchanged, and the machine formats suppress all
  human-readable stdout so the output stays parseable by scripts and CI.
- CLI: `--media-resolution low|medium|high|ultra_high` on `browser` /
  `android` / `ios` / `desktop` — screenshot detail the model sees
  (gemini-vertex only; default `QIRA_MEDIA_RESOLUTION`, else `high`). The
  constructor parameter and env var already existed; this exposes them as a
  task option alongside `--thinking-level`.

**Changed**

- Engine: the completed-steps summary is run-length compressed at render
  time (13 consecutive scrolls become one `scroll ×13` line — cheaper and
  makes loops more visible to the model) and capped at 200 lines: beyond
  that the oldest lines are dropped and replaced with an
  `(earliest N actions omitted)` marker, bounding per-step prompt growth on
  very long (hundreds of steps) tasks.
- Engine: cache-friendly prompt layout for Gemini implicit caching. The step
  summary and saved notes moved from the (head-of-prompt) system instruction
  to a progress-context message near the tail of the conversation, and the
  history window now truncates in batches (at 2x `max_entries`, folding back
  to `max_entries`) instead of sliding one entry per step. Both system-prompt
  halves and the replayed text history are now byte-stable across steps, so
  the provider prefix cache covers system + tools + text history on every
  step and only breaks once per `max_entries` steps. Claude behavior is
  unchanged (its window never truncates within a task).

**Fixed**

- Engine: history screenshots no longer accumulate across steps. A stale
  screenshot stayed attached to every history entry instead of only the most
  recent one, so each decide request carried up to 6 images instead of 2 —
  roughly doubling per-step input tokens on multi-step tasks.

**Unchanged**

- The automation API: `bot.ai()`, `bot.click/type/extract/verify/locate`,
  `knowledge=`, `custom_tools=`, `on_step`, `thinking_level`
  (minimal/low/medium/high), backends (browser/Android/iOS/desktop/Windows
  window), HTML reports, screen/device recording, and the progress overlay.

**Keeping v2**

The v2 cloud behavior remains available by pinning the previous major
version: `pip install "qirabot<3"`.
