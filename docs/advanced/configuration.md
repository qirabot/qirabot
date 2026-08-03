---
title: Configuration
description: Every Qirabot knob - model and Vertex AI setup, Google Cloud credentials, constructor options, environment variables, thinking level and per-call overrides, response language, and settle delay tuning.
---

# Configuration

The decision engine runs locally inside the SDK and calls your own model
endpoint on Google Vertex AI. Configuration is therefore two things: Google
Cloud credentials, and which model to use.

```python
from qirabot import Qirabot

bot = Qirabot()  # model param > QIRA_MODEL env var > gemini-vertex/gemini-3.6-flash
```

**Credentials** are standard Google Cloud Application Default Credentials
(ADC): set `GOOGLE_APPLICATION_CREDENTIALS` to a service-account JSON file,
run `gcloud auth application-default login` once, or run on GCE where the
metadata server provides them. `qirabot doctor` and `qirabot models` both
report whether ADC resolves on the machine.

Settings can also live in a project `.env`: scripts opt in explicitly —
`from qirabot import load_dotenv; load_dotenv()` — which reads
`$QIRA_DOTENV` or `./.env` and never overrides exported variables. The CLI
loads `.env` automatically; the SDK never reads it on its own. Typical
`.env` contents are `QIRA_MODEL` and `QIRA_VERTEX_PROJECT`.

## Constructor options

| Parameter | Env Variable | Default | Description |
|---|---|---|---|
| `model` | `QIRA_MODEL` | `gemini-vertex/gemini-3.6-flash` | Model as `{provider}/{model}` ([details](#model-language)) |
| `vertex_project` | `QIRA_VERTEX_PROJECT` | see below | Google Cloud project for the Vertex call |
| `vertex_location` | `QIRA_VERTEX_LOCATION` | `"global"` | Vertex location/region |
| `thinking_level` | — | `"low"` | Thinking level for all operations: `minimal` / `low` / `medium` / `high` ([details](#thinking-level)) |
| `media_resolution` | `QIRA_MEDIA_RESOLUTION` | `"high"` | Screenshot detail the model sees: `low` / `medium` / `high` / `ultra_high` (Gemini only); lower it to cut image tokens per step |
| `language` | — | model default | Response language, e.g. `"zh"` / `"en"` |
| `task_name` | — | `""` | Task name (shown in the HTML report) |
| `locate_format` | `QIRA_LOCATE_FORMAT` | `""` | Element-location output format; `bbox_yx_1000` switches to normalized y/x bounding boxes |
| `report` | — | `True` | Write an HTML run report on close |
| `report_dir` | `QIRA_REPORT_DIR` | `./qira_runs/...` | Report output root |
| `record` | `QIRA_RECORD` | `False` | Record the screen (ffmpeg) |
| `record_fps` | — | `12` | Recording frame rate |
| `record_window` | `QIRA_RECORD_WINDOW` | `False` | Windows: record just the window under test |
| `record_audio` | `QIRA_RECORD_AUDIO` | `False` | Windows: capture system audio |
| `record_audio_offset` | `QIRA_AUDIO_OFFSET` | `None` | A/V sync offset in seconds |
| `record_device` | `QIRA_RECORD_DEVICE` | `False` | Record the device screen (adb / Appium) |
| `record_mjpeg_url` | `QIRA_RECORD_MJPEG_URL` | `None` | Record an MJPEG stream (iOS WDA) |
| `screenshot_annotate` | — | `True` | Red crosshair at click/type coordinates |
| `screenshot_format` | — | `"jpeg"` | `"jpeg"` or `"png"` |
| `screenshot_quality` | — | `80` | JPEG quality, 1–100 |
| `retry` | — | `1` | Retries per action on transient failures (also a per-call kwarg: `bot.click(..., retry=3)`) |
| `retry_delay` | — | `1.0` | Seconds between retries |
| `settle_seconds` | `QIRA_SETTLE_SECONDS` | per-platform | Pause after each action before the next screenshot |
| `overlay` | — | `False` | Always-on-top progress window + ESC kill switch ([details](/advanced/overlay)) |

What the `record*` knobs actually produce (formats, per-platform mechanics,
where the file lands) is covered in [Reports & Recording](/advanced/reports).

**Project and location resolution.** The Vertex project is resolved as:
`vertex_project=` param > `QIRA_VERTEX_PROJECT` > `GOOGLE_CLOUD_PROJECT` >
the project id carried by the ADC credentials themselves. The location:
`vertex_location=` param > `QIRA_VERTEX_LOCATION` > `GOOGLE_CLOUD_LOCATION`
> `"global"`. The CLI exposes the same pair as global flags before the
subcommand: `qirabot --vertex-project my-proj --vertex-location us-east5
browser "..."`.

Env-only overrides with no constructor equivalent:

| Env Variable | Description |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Standard Google variable: path to a service-account JSON for ADC |
| `QIRA_ADB_PATH` | Explicit adb binary for the Android backend |
| `QIRA_SCREEN_INDEX` | Which monitor to record on multi-display machines |
| `QIRA_AUDIO_DEVICE` | Recording audio device (Windows) |
| `QIRA_DOTENV` | Path `load_dotenv()` reads instead of `./.env` |
| `QIRA_RECORD_WINDOW_NATIVE` | Windows: force legacy gdigrab per-window capture instead of the crop-from-desktop default |
| `QIRA_TEXT_FALLBACK` | Windows: `unicode` reverts non-ASCII typing from clipboard-paste to unicode injection |
| `QIRA_MODIFIER_LEAD` / `QIRA_MODIFIER_TAIL` | Seconds around a modifier-held click (desktop adapters) |
| `QIRA_OVERLAY_DEBUG` | `1` lets the overlay helper's stderr through for diagnosis |
| `QIRA_ENGINE_TRACE` | Debug: a directory; appends one JSONL record per model call and saves the step screenshots there |

## Model & language

`model` selects which model backs every operation, in the form
`"{provider}/{model}"`:

| Provider | Serves | Auth | Default model |
|---|---|---|---|
| `gemini-vertex` | Google Gemini models on Vertex AI | ADC, or a Vertex AI API key (`vertex_api_key=` / `QIRA_VERTEX_API_KEY`) | `gemini-3.6-flash` |
| `gemini` | Google Gemini models via the Gemini Developer API | AI Studio API key (`gemini_api_key=` / `QIRA_GEMINI_API_KEY` / `GEMINI_API_KEY`) | `gemini-3.6-flash` |

```python
bot = Qirabot(model="gemini-vertex/gemini-3.6-flash")
bot = Qirabot(model="gemini")  # bare provider → its default model
```

A bare provider name resolves to that provider's default model. Unset
everything and the SDK uses `gemini-vertex/gemini-3.6-flash`.
`qirabot models` lists the providers, their default models, and whether
the configured auth resolves.

There is no per-step billing by Qirabot — model calls go directly from your
machine to your Vertex AI project and are billed by Google Cloud at that
model's rates.

**Watching cost:** `extract()` / `verify()` results and each `StepResult`
from `ai()` carry `input_tokens` / `output_tokens` fields — a call's spend
is their sum. See the
[Method Reference](/reference/methods#result-objects).

## Thinking level

`thinking_level` scales reasoning depth within the same model — deeper
thinking for hard judgment calls, shallower for obvious ones:

| Value | Trade-off |
|---|---|
| `minimal` | Fastest, cheapest — obvious targets, clean UIs |
| `low` | The default — fast steps, enough reasoning for routine UI decisions |
| `medium` | Harder judgment calls |
| `high` | Deepest reasoning — highest latency and thinking-token spend |

```python
bot = Qirabot(thinking_level="low")                           # task-wide default
bot.verify(page, "the discount was applied to every row",
           thinking_level="high")                             # hard call → think more
```

The constructor sets the task-wide default, every action method takes a
per-call override. Deeper thinking burns more thinking tokens, so the
cost-control pattern is: stay low by default, raise only the hard calls.

One caveat: the effective granularity depends on the underlying model; some
models merge or clamp adjacent levels, so treat the value as an intent, not
a guarantee of four distinct depths.

`language` sets the language of AI responses (extracted text, reasoning) —
a short tag like `"zh"` or `"en"`:

```python
bot = Qirabot(language="zh")
text = bot.extract(page, "Get the main heading", language="zh")
```

## Settle delay

After every screen-changing action each adapter pauses briefly so the UI
repaints before the next screenshot — without it the model can capture a
mid-animation frame and wrongly conclude the action did nothing. Defaults
are tuned per platform (desktop/Android `1.0`s, Selenium/Appium/WDA `0.6`s;
Playwright relies on its own auto-waiting and adds none).

```python
bot = Qirabot(settle_seconds=1.5)   # laggy remote device: wait longer
bot = Qirabot(settle_seconds=0.3)   # fast local app: go quicker
bot = Qirabot(settle_seconds=0)     # disable; lean on wait_for() instead
```

This is a blunt fixed delay. For "wait until X appears" prefer the auto-wait
`timeout=` / `wait_for()` polling — it returns as soon as the condition
holds.

## Run lifecycle

Each `Qirabot` instance manages a local run: a run id (`local-` plus 8 hex
characters, readable via `bot.task_id`) is assigned on construction, every
call is recorded as a step, and the HTML report is written on `close()` /
context-manager exit. If `close()` is never called, `atexit` cleans up. The
constructor validates the model configuration and Google Cloud credentials,
so a bad setup fails at construction, not mid-run. To end a run as failed
or cancelled instead of completed, see `fail()` / `cancel()` in the
[API reference](/reference/api#task-lifecycle).
