---
title: Configuration
description: Every Qirabot knob - model and Vertex AI setup, Google Cloud credentials, constructor options, environment variables, thinking level and per-call overrides, response language, and settle delay tuning.
---

# Configuration

The decision engine runs in your own process and calls your model
endpoint on Google Vertex AI. Configuration is therefore two things: Google
Cloud credentials, and which model to use.

```python
from qirabot import Qirabot

bot = Qirabot()  # model param > QIRA_MODEL env var > gemini-vertex/gemini-3.8-flash
```

**Credentials** are standard Google Cloud Application Default Credentials
(ADC): set `GOOGLE_APPLICATION_CREDENTIALS` to a service-account JSON file,
run `gcloud auth application-default login` once, or run on GCE where the
metadata server provides them. `qirabot doctor` and `qirabot models` both
report whether ADC resolves on the machine.

Settings can also live in a project `.env`. Scripts opt in explicitly with
`from qirabot import load_dotenv; load_dotenv()`, which reads
`$QIRA_DOTENV` or `./.env` and never overrides exported variables. The CLI
loads `.env` automatically; the SDK never reads it on its own. Typical
`.env` contents are `QIRA_MODEL` and `QIRA_VERTEX_PROJECT`.

## Constructor options

| Parameter | Env Variable | Default | Description |
|---|---|---|---|
| `model` | `QIRA_MODEL` | `gemini-vertex/gemini-3.8-flash` | Model as `{provider}/{model}` ([details](#model-language)) |
| `vertex_project` | `QIRA_VERTEX_PROJECT` | see below | Google Cloud project for the Vertex call |
| `vertex_location` | `QIRA_VERTEX_LOCATION` | `"global"` | Vertex location/region |
| `vertex_api_key` | `QIRA_VERTEX_API_KEY` | `""` | [Vertex AI API key](https://cloud.google.com/vertex-ai/generative-ai/docs/start/api-keys) instead of ADC — no gcloud setup. `gemini-vertex` only, always the global endpoint, and it overrides `vertex_project`/`vertex_location`. Not an AI Studio key; `GOOGLE_API_KEY` is deliberately never read |
| `gemini_api_key` | `QIRA_GEMINI_API_KEY`, `GEMINI_API_KEY` | `""` | [AI Studio API key](https://ai.google.dev/gemini-api/docs/api-key) for the `gemini` provider (Gemini Developer API, no Google Cloud involved) |
| `service_tier` | `QIRA_SERVICE_TIER` | `"standard"` | Consumption tier: `standard` / `flex` / `priority` ([details](#service-tier)) |
| `tier_escalation` | `QIRA_TIER_ESCALATION` | `False` | Retry one rung up the tier ladder when a tier runs out of capacity ([details](#service-tier)) |
| `thinking_level` | — | `"low"` | Thinking level for all operations: `minimal` / `low` / `medium` / `high` ([details](#thinking-level)) |
| `media_resolution` | `QIRA_MEDIA_RESOLUTION` | `"high"` | Screenshot detail the model sees: `low` / `medium` / `high` / `ultra_high` (Gemini only; `ultra_high` needs a Gemini 3 model); lower it to cut image tokens per step |
| `language` | — | instruction's language | Response language: a tag (`"zh"`, `"ja"`, `"de"`, …) or any language name |
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
| `gemini-vertex` | Google Gemini models on Vertex AI | ADC, or a Vertex AI API key (`vertex_api_key=` / `QIRA_VERTEX_API_KEY`) | `gemini-3.8-flash` |
| `gemini` | Google Gemini models via the Gemini Developer API | AI Studio API key (`gemini_api_key=` / `QIRA_GEMINI_API_KEY` / `GEMINI_API_KEY`) | `gemini-3.8-flash` |

```python
bot = Qirabot(model="gemini-vertex/gemini-3.8-flash")
bot = Qirabot(model="gemini")  # bare provider → its default model
```

A bare provider name resolves to that provider's default model. If nothing
is set, the SDK uses `gemini-vertex/gemini-3.8-flash`.
`qirabot models` lists the providers, their default models, and whether
the configured auth resolves.

There is no per-step billing by Qirabot. Model calls go directly from your
machine to your Vertex AI project and are billed by Google Cloud at that
model's rates.

**Watching cost:** `extract()` / `verify()` results and each `StepResult`
from `ai()` carry `input_tokens` / `output_tokens` fields; a call's spend
is their sum. See the
[Method Reference](/reference/methods#result-objects). Where those tokens
actually go, and which knobs on this page move them, is in
[Controlling Cost](/advanced/cost).

## When a call fails

Two different layers retry, and they are easy to confuse because they use
the same words. The `retry=` and `timeout=` options in the
[Method Reference](/reference/methods) are **yours**: `timeout=` polls the
screen until an element looks present, `retry=` repeats a whole action.
Underneath, the engine's own model calls have fixed budgets you do not
configure. This section is that lower layer.

### Per-call budgets

| Call | Budget |
|---|---|
| `ai()` step decision, `extract()`, `verify()` | 120s |
| `locate()` | 60s |
| Connecting to the endpoint | 5s |

`locate()` is half of a decision because the engine retries a locate once —
two locate shots should cost about what one decision does. Connecting is
separate because reaching the endpoint has nothing to do with how long the
model thinks; an unreachable host reports itself in seconds rather than
holding the whole budget.

### What gets retried

| Failure | Behaviour |
|---|---|
| Rate limit (429) | Backs off 5s → 10s → 20s → 30s, five attempts, about a minute |
| Refusal (503), connect or pool failure | Backs off 1s → 2s, three attempts |
| The model answered too slowly (read timeout, 504) | **Fails immediately** |
| Bad request, auth, not found (400/401/403/404) | Fails immediately |
| Empty or unparseable reply | Re-asked once with corrective feedback |

Every backoff delay carries ±20% jitter, so concurrent runs sharing a quota
do not retry in lockstep and collide again.

The two timing rules are worth knowing because they explain most of what you
will see in a log. **Rate limits get a long wait** because quotas are rolling
per-minute windows: waiting one out is free and usually works, and a run that
died on its first quota brush would throw away all its progress. **A slow
answer is never retried**, because the request did reach the model — asking
the same question again costs another full budget to be told the same thing.
That is the difference between a read timeout and a connect timeout, which
look alike but mean opposite things.

### How long a run can take

Nothing caps an `ai()` run in wall clock. `max_steps` (default 20) is the
bound, so budget with it: a step costs at most two model calls — the
decision, plus one re-ask if the reply comes back unusable.

## Service tier

`service_tier` picks how your requests are scheduled against Google's
capacity. It moves price and latency in opposite directions:

| Tier | Price | Latency | Availability |
|---|---|---|---|
| `flex` | ~50% of standard | Queued — see the warning below | Sheddable — can be refused under load |
| `standard` (default) | Baseline | Seconds | Best-effort |
| `priority` | ~75–100% above standard | Seconds, scheduled ahead of standard traffic | Downgraded to standard when over capacity |

```python
bot = Qirabot(model="gemini-vertex/gemini-3.8-flash", service_tier="priority")
```

::: warning Check what flex costs you before relying on it
Flex capacity is queued, and on Vertex the wait behaves like a fixed cost per
request rather than one proportional to response length. That matters here:
a decide or locate call produces a short response, so it pays the full delay
with nothing to amortize it against — and an `ai()` run makes one call per
step, so the cost multiplies by step count. On the Gemini Developer API the
same requests were scheduled far more cheaply.

How big the delay actually is varies by model, endpoint load and time of day,
so measure it on your own traffic rather than trusting a number from
elsewhere. The per-step timings in the [run report](/advanced/reports) make
the comparison a two-run experiment.

The rule of thumb: flex suits one-shot, unattended work where a late answer
costs nothing. Interactive automation is where a queued tier hurts most.
:::

**You are billed for what is served, not what you ask for.** A tier the
endpoint cannot place is served — and charged — at standard rates.

### When a tier is not honoured

A downgrade produces no error. The response is an ordinary `200` with no
error field and no explanatory header; the only tell is the served-tier
field, which Qirabot checks on every call (`usageMetadata.trafficType` on
Vertex, the `x-gemini-service-tier` response header on the Gemini Developer
API). On a mismatch it logs one warning per session:

```
gemini-vertex: requested the priority tier but the request was served as
standard — billed at standard rates, and the endpoint gives no reason.
Config seen: model=gemini-3.8-flash location=global. …
```

There are three causes, and the warning prints the model and endpoint it
used so you can rule out the first two at a glance:

1. **Wrong endpoint.** Vertex serves the non-standard tiers on the global
   endpoint only, and accepts the header off a regional one while ignoring
   it. Qirabot rejects a regional `vertex_location` at construction, so if
   the bot was created at all, this is not your cause.
2. **The model does not support the tier.** Coverage differs per tier and
   changes over time; check Google's list for
   [Vertex](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/priority-paygo)
   or the [Gemini Developer API](https://ai.google.dev/gemini-api/docs/pricing).
   The two tiers fail differently here: flex on an unsupported model is a
   hard `400` naming it (`Flex API is not supported for model: …`), while
   priority just downgrades. So if you asked for flex and got a result at
   all, the model supports it.
3. **Entitlement or capacity.** Vertex Priority PayGo carries
   organization-level ramp limits, and the Gemini Developer API gates
   priority behind its higher paid tiers. No response field reports this, so
   check your quotas in the Cloud Console or ask whoever owns the account.

To separate an entitlement problem from anything in your own setup, take
Qirabot out of the loop and ask the endpoint directly:

```bash
curl -sS -X POST \
  "https://aiplatform.googleapis.com/v1/projects/PROJECT/locations/global\
/publishers/google/models/gemini-3.8-flash:generateContent" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Vertex-AI-LLM-Shared-Request-Type: priority" \
  -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}' \
  | grep trafficType
```

`ON_DEMAND_PRIORITY` means the tier works and something in the SDK config is
at fault; plain `ON_DEMAND` means the account cannot get it, and no client
change will help. Swap the header for `flex` to check that tier.

The run report gives the tier its own header row next to the model. When
the two differ it says so in words — `priority — served as standard, and
billed at that rate` — and an unconfigured run gets no row at all.

A downgrade is **not** a failure and does not trigger `tier_escalation`: the
call succeeded, at standard price. Escalation reacts to capacity *failures*
— a rate limit, a refusal, or a flex request still queued when its budget
expires.

### Escalating on exhaustion

With `tier_escalation=True`, a tier that runs out of capacity is retried one
rung up — `flex` → `standard` → `priority` — and **stays there for the rest
of the session**, instead of failing the run:

```python
bot = Qirabot(service_tier="standard", tier_escalation=True)
```

Out of capacity means a rate limit (429), a refusal (503), or — on flex only
— a request whose budget expires while still queued. Escalation happens after
the ordinary retries for that failure ([When a call fails](#when-a-call-fails))
are exhausted, so how soon the handoff comes depends on which failure it was:

| Failure | Handoff |
|---|---|
| Rate limit | After the full quota window, roughly a minute |
| Refusal | A few seconds |
| Flex still queued | As soon as the call's timeout expires |

Escalating is always the last move before a long `ai()` run dies and loses
its accumulated progress, never the first response to a failure — the free
remedies come first. That is why a rate limit waits: the window is rolling
and costs nothing to sit through, while escalating raises the rate. The
escalated call does not wait out a second window, though — the tiers commonly
draw on the same quota, so another minute of sleeping would stall the run for
capacity that is not coming.

Moving is permanent for the life of the bot because the alternative is
paying to rediscover the same congestion on every step. A new `Qirabot`
probes again.

It also changes what a flex attempt is worth waiting for. With escalation on
it is a probe you are happy to abandon, so it gets a short leash rather than
the widened budget; with escalation off it keeps the widened budget, since
waiting is the only option left. Either way flex retries like any other tier
— a refusal comes back at once, so trying again costs seconds while
escalating doubles the rate. Only a probe that burns its whole budget hands
off immediately, because repeating it is the one expensive move.

The effect on a congested tier is the whole point. A 20-step run against a
queued flex endpoint, standard healthy:

| | Wall clock | Steps completed |
|---|---|---|
| `tier_escalation=False` | one full timeout per step | none |
| `tier_escalation=True` | one probe, then standard speed | all |

It is off by default because escalating can raise your per-token rate — but
the downside is bounded by the same billing rule above: escalating to
`priority` costs more only if priority capacity is actually what serves the
request.

## Thinking level

`thinking_level` scales reasoning depth within the same model: deeper
thinking for hard judgment calls, shallower for obvious ones.

| Value | Trade-off |
|---|---|
| `minimal` | Fastest and cheapest; obvious targets, clean UIs |
| `low` | The default; fast steps, enough reasoning for routine UI decisions |
| `medium` | Harder judgment calls |
| `high` | Deepest reasoning; highest latency and thinking-token spend |

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

`language` sets the language of AI responses (extracted text, reasoning).
Common tags (`"zh"`, `"ja"`, `"ko"`, `"de"`, `"fr"`, …) map to the language
they name; any other value — a rarer tag or a plain language name — is passed
to the model as written. Unset, responses follow the language the instruction
is written in:

```python
bot = Qirabot(language="zh")
text = bot.extract(page, "Get the main heading", language="zh")
```

## Settle delay

After every screen-changing action each adapter pauses briefly so the UI
repaints before the next screenshot. Without the pause the model can
capture a mid-animation frame and wrongly conclude the action did
nothing. Defaults
are tuned per platform (desktop/Android `1.0`s, Selenium/Appium/WDA `0.6`s;
Playwright relies on its own auto-waiting and adds none).

```python
bot = Qirabot(settle_seconds=1.5)   # laggy remote device: wait longer
bot = Qirabot(settle_seconds=0.3)   # fast local app: go quicker
bot = Qirabot(settle_seconds=0)     # disable; lean on wait_for() instead
```

This is a blunt fixed delay. For "wait until X appears" prefer the auto-wait
`timeout=` / `wait_for()` polling, which returns as soon as the condition
holds.

## Run lifecycle

Each `Qirabot` instance manages a local run: a run id (8 hex characters,
readable via `bot.task_id`) is assigned on construction, every
call is recorded as a step, and the HTML report is written on `close()` /
context-manager exit. If `close()` is never called, `atexit` cleans up. The
constructor validates the model configuration and Google Cloud credentials,
so a bad setup fails at construction, not mid-run. To end a run as failed
or cancelled instead of completed, see `fail()` / `cancel()` in the
[API reference](/reference/api#task-lifecycle).
