# Changelog

## 3.3.0 (2026-08-16)

### Consumption tiers: flex and priority

- New `service_tier=` (`QIRA_SERVICE_TIER`, `--service-tier`) selects
  `standard` (default), `flex` — roughly half price for slower, sheddable
  capacity — or `priority`, a premium for capacity scheduled ahead of
  standard traffic. Works on both providers: Vertex takes it as the
  `X-Vertex-AI-LLM-Shared-Request-Type` header, the Gemini Developer API as
  a top-level `service_tier` body field.
- The tier you ask for is not always the tier you get, and billing follows
  what was served. Every response is checked against the request —
  `usageMetadata.trafficType` on Vertex, the `x-gemini-service-tier`
  response header on the Gemini Developer API — and a mismatch logs one
  warning per session. A regional `vertex_location` is rejected at
  construction, since Vertex accepts the header off the global endpoint and
  silently ignores it.
- Flex widens the per-call timeout by 1.5x, and on the Gemini Developer API
  bounds the queue wait server-side (`X-Server-Timeout`) so an interactive
  step fails fast instead of hanging.
- New `tier_escalation=` (`QIRA_TIER_ESCALATION`, `--tier-escalation`), off
  by default: when a tier runs out of capacity, retry once one rung up
  (`flex` → `standard` → `priority`) rather than losing an in-progress
  `ai()` run. A rate limit hands off only after the backoff schedule is
  exhausted — waiting out a rolling quota window is free, escalating is
  not. A refusal hands off at once, and so does a flex request still queued
  when its budget expires: on flex a timeout *is* the capacity signal, and
  retrying a queue that just stalled costs a full widened timeout for
  nothing. The escalated call itself skips the quota window: the tier below
  already waited one out and the tiers commonly share the bucket.
- Escalation is sticky. It applies to the whole session, not one call —
  otherwise a congested tier charges every step of an `ai()` run the full
  cost of rediscovering the same congestion. A flex attempt is also given a
  short leash rather than the widened budget while escalation is on, since
  it is a probe worth abandoning. Against a queued flex endpoint with
  standard healthy, a 20-step run goes from completing nothing to
  completing everything after a single probe.

- The run report gives the tier its own header row, spelling out the served
  side when it differs from the one requested. Which tier ran decides the
  per-token rate, so a report that omitted it could not be reconciled
  against a bill.

### The run report header is labelled

- The header was two dot-separated lines of bare fragments — an opaque id, a
  model name, `in/out/think`, a duration of something unstated — which left
  the reader to work out what each string was. It is now a label/value grid:
  Run, Model, Tier, Steps, Tokens, Time, Run ID, each row present only when
  it has something to say.
- Token counts are named rather than abbreviated (`15.5k total — 15.1k
  prompt, 410 response`), and the duration says what it measures. The report
  now also shows how much of the step time was spent waiting on the model,
  a figure the timeline had always collected but never rendered.

### Fix: the run id names the directory it belongs to

- The output directory was built from `task_id[:8]`, but the id carried a
  `local-` prefix that ate six of those characters — a run reported as
  `local-40746eb1` landed in `100621-local-40`. The console prints the id and
  never the path, so the one link between them didn't line up. Worse, the
  truncation threw away the id's uniqueness: two clients constructed in the
  same second had a 1-in-256 chance of sharing an output directory and
  interleaving their screenshots.
- `bot.task_id` is now bare hex (`40746eb1`) and the directory carries it
  whole (`qira_runs/2026-08-16/100621-40746eb1/`). The `local-` prefix dated
  from the cloud service, where there was another kind of id to tell it
  apart from; every run is local now, so it distinguished nothing.
- Breaking for anything parsing `bot.task_id` or the `task_id` field of
  `--output-format json` / `stream-json`: the value no longer starts with
  `local-`.

### Fix: no step is left without a screenshot in the report

- A step that follows `save_note` is decided on the previous step's frame:
  the note doesn't touch the device, so nothing is re-captured. That
  optimisation was also deciding whether the report got a picture, so those
  steps rendered an empty screenshot cell — indistinguishable from a lost
  capture, and on the common `save_note` → `done` ending it meant the report
  had no final frame at all. A six-step extraction run showed four images.
- Such a step now shows the frame it was decided on, labelled *frame from an
  earlier step* so a reused image can't be read as a fresh capture. No
  duplicate file is written: the entry points at the frame already on disk,
  unless the step marks coordinates, where the annotation is new information
  and gets its own copy.

### Fix: a slow model call is no longer retried like a transport blip

- A read timeout means the request reached the model and the answer did not
  come back inside the budget. It was being retried on the generic schedule,
  so a hanging endpoint cost three full per-call budgets — over six minutes
  on a decide — to re-ask a question that was already too slow once. Read
  timeouts (and 408/504) now fail on the first occurrence; a worst-case
  decide drops from ~363s to ~120s.
- Failures that never reached the model — connect, write and pool timeouts —
  are classified `UNAVAILABLE` instead of `TIMEOUT` and stay retryable, which
  is what the old blanket rule was actually for.
- Connect gets its own 5s budget rather than inheriting the call's. An
  unreachable endpoint used to take the full timeout to report itself;
  including retries it now surfaces in under 20s.

### Fix: retry backoff is jittered

- Both retry schedules were fixed constants, so concurrent qirabot
  processes brushing the same per-minute quota — a CI matrix, a
  multi-device run — retried in lockstep and kept colliding. Every delay is
  now spread by ±20%.

## 3.2.0 (2026-08-10)

### Breaking: the cloud-era exceptions are gone

- `InsufficientBalanceError`, `RateLimitError`, `QirabotConnectionError`
  and `TaskTerminatedError` have been removed from `qirabot` and
  `qirabot.exceptions`. v3 has no Qirabot server, no per-step billing and
  no server-side task state, so none of them had anything left to
  describe — they had been exported as never-raised stubs since 3.0.
  Importing them now raises `ImportError`.
- Migration: delete the `except` clauses written against them, or widen
  them to `QirabotError`. Model-provider quota and connectivity problems
  surface as `ActionError` (mid-run) or `AuthenticationError` (at
  construction); rate limits are still retried inside the provider layer
  and only surface as `ActionError` if they persist.

### Fix: stale screen geometry after an iOS rotation

- The WDA adapter cached the device's point size on first use, but WDA
  reports the size of the *current* orientation. After a rotation, every
  coordinate derived from `device_info()` — swipe gestures, annotation
  scale — was computed against the pre-rotation screen. Rotation is now
  detected from the screenshot frame itself, and the cached point size
  and annotation scale are dropped when it flips.

## 3.1.3 (2026-08-10)

### Fix: stray Playwright teardown noise after `qirabot doctor`

- `doctor` no longer occasionally trails "Task was destroyed but it is
  pending!" / `TargetClosedError` after its last line. The Chromium
  probe now runs Playwright in a subprocess, so the sync API's
  quick-start/stop teardown race (seen on Windows) stays out of the
  command's output. Diagnostics are unchanged.

## 3.1.2 (2026-08-10)

### Fix: ADC authentication in isolated installs

- `requests` is now a declared dependency. google-auth's token-refresh
  transport imports it at call time but only lists it as an optional
  extra, so isolated installs (`uv tool install`, pipx) resolved ADC
  credentials and then failed the token refresh — `qirabot doctor` and
  every ADC-authenticated run reported a raw ImportError as a
  credentials failure.
- A missing transport now surfaces as `failed to refresh Google Cloud
  credentials: ...` with auth context instead of an unwrapped
  ImportError.

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

### The decision engine runs locally

The engine runs inside the SDK process: each screenshot is sent directly from
your machine to a vision model you configure on Google Vertex AI,
authenticated with your own Google Cloud credentials (Application Default
Credentials). No Qirabot account, no API key, no per-step billing, and no
Qirabot server in the loop.

**Configuration**

- Authentication is Google Cloud ADC: set `GOOGLE_APPLICATION_CREDENTIALS` to
  a service-account JSON, run `gcloud auth application-default login`, or run
  on GCE (metadata server).
- Model selection is explicit and local:
  `Qirabot(model="{provider}/{model}")` or `QIRA_MODEL`, with provider one of
  `claude-vertex` / `gemini-vertex`. Default:
  `gemini-vertex/gemini-3.6-flash`.
- Google Cloud project/region: `vertex_project=` / `vertex_location=`
  constructor parameters, `--vertex-project` / `--vertex-location` CLI global
  options, or `QIRA_VERTEX_PROJECT` / `QIRA_VERTEX_LOCATION` (falling back to
  `GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION`, then to the project
  carried by the ADC credentials; location defaults to `global`).
- `task_id` is a local run id of the form `local-<hex>`; nothing is synced to
  a server. `fail()` / `cancel()` record the run's terminal outcome locally
  for the HTML report.
- New required dependency: `google-auth`.
- CLI: `qirabot models` lists the Vertex providers, their default models, and
  checks ADC; `qirabot doctor` checks Python, Google Cloud credentials (ADC +
  project), and backend dependencies.

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
