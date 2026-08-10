---
title: Controlling Cost — What a Step Costs and Which Levers Move It
description: "Where a Qirabot step's tokens actually go — tool schemas, system prompt, screenshots — and the levers that reduce them: media_resolution, exclude_tools, thinking_level, deterministic steps instead of ai(), and how to measure spend."
---

# Controlling Cost

Qirabot itself bills nothing. Model calls go straight from your machine to
your Vertex AI project (or the Gemini Developer API) and are billed by
Google at that model's rates. So "cost" means one thing: tokens per model
call, times the number of calls.

## Which calls cost anything

Every AI call sends a screenshot and costs tokens: `ai()` (once per step),
`click` / `type_text` / `double_click` with an element description,
`extract`, `verify`, `locate`, and each `wait_for` poll.

These make no model call at all and are free: `navigate`, `go_back`,
`close_tab`, `scroll`, `press_key`, `screenshot`, `launch_app`,
`key_down` / `key_up`, `type_text` with an empty `locate`, and `mouse_up`
without a locate. They're marked "no AI" throughout the
[API reference](/reference/api).

## Where a step's tokens go

A decide request carries four things: the system prompt, the tool schemas,
the replayed text history, and images — the current screenshot plus the one
most recent history screenshot.

The proportions are the useful part. Measured on a Chrome task at
`media_resolution="medium"`, a first step came to roughly 3,400 input
tokens:

| Part | ≈ tokens | share |
|---|---|---|
| Tool schemas (14 built-ins on Chrome) | 2,040 | 59% |
| System prompt | 850 | 25% |
| Current screenshot (`medium`) | 520 | 15% |
| Instruction text | 30 | 1% |

The counter-intuitive result: **the screenshot is not the expensive part —
the tool schemas are.** They are also fixed cost, paid on every single step.

Image tokens are set purely by the resolution tier, not by the image's
pixel size or JPEG quality:

| `media_resolution` | ≈ image tokens (1280×800) |
|---|---|
| `low` | 273 |
| `medium` | 522 |
| `high` (default) | 1,092 |
| `ultra_high` | higher still |

Shrinking or re-compressing the screenshot client-side changes nothing —
the tier decides. Consequently `screenshot_quality` and `screenshot_format`
are **not** cost levers; they only affect the files on disk and upload size.

::: tip These are magnitudes, not guarantees
Measured against a Gemini 3 flash endpoint with a 1280×800 screenshot. Exact
counts move with the model, the platform's tool set, and the screenshot's
aspect ratio. Measure your own workload with `bot.usage` before optimizing.
:::

## The levers, most effective first

**1. Use deterministic steps where the flow is known.** An `ai()` run
spends one call per step *and* re-sends the tool schemas each time. A known
login is three cheap calls; handing it to `ai()` can be ten expensive ones.
Reserve `ai()` for the genuinely dynamic tail of a flow.

```python
bot.type_text(page, "the Username field", "standard_user")
bot.type_text(page, "the Password field", "secret_sauce")
bot.click(page, "the Login button")
result = bot.ai(page, "complete checkout as John Doe, zip 10001")  # dynamic part
```

**2. Lower `media_resolution`.** Going from the `high` default to `medium`
saves ~570 image tokens per step, `low` saves ~820. Clean, high-contrast UIs
with large targets usually survive `medium` fine; dense tables, small text
and games are where `high` earns its cost.

```python
bot = Qirabot(media_resolution="medium")     # or QIRA_MEDIA_RESOLUTION / --media-resolution
```

**3. Prune tools with `exclude_tools`.** The biggest fixed cost is the one
most people never touch. A task that only clicks and types doesn't need
`drag`, `long_press`, `hover`, or `scroll_at` in every request:

```python
bot.ai(page, "…", exclude_tools=["drag", "hover", "scroll_at", "double_click"])
```

Built-in count per platform: Chrome 14, desktop 17, Android/iOS 12, Windows
window 11. `done` can't be excluded. This also keeps the model from
wandering into actions the task never needs. See
[AI Tasks & Custom Tools](/advanced/ai-tasks).

**4. Keep `thinking_level` at `low` and raise only hard calls.** Thinking
tokens are output tokens. The default is already `low`; the pattern is to
lift it per call, not per bot:

```python
bot.verify(page, "the discount applied to every row", thinking_level="high")
```

**5. Widen `wait_for` intervals.** Each poll is a full verify call. The
default `interval=2.0` against `timeout=30.0` is up to 15 billed calls.
Where a slow page is expected, `interval=5.0` cuts that by more than half.

**6. Treat `max_steps` as a stop-loss, not a tuning knob.** It caps the
damage of a run that goes nowhere; it doesn't make a successful run cheaper.
A `max_steps` result means the budget was too small — see
[Error Handling](/advanced/error-handling).

## On prompt caching

The engine lays out prompts so the cacheable prefix (system prompt + tool
schemas + text history) is byte-stable across steps, and the history window
truncates in batches so that prefix only breaks once every few steps.

Whether that translates into a discount is Google's call: the engine does not
use the explicit `cachedContent` API, so it depends entirely on Gemini's
implicit cache — which in our own measurements frequently does not hit.
Treat `cache_read_tokens` as a bonus when it appears, not as a budget you can
plan around. `cache_write_tokens` is always `0`.

## Measuring actual spend

```python
bot = Qirabot()
...
u = bot.usage                      # frozen snapshot; read again for fresh numbers
print(u.ai_steps, u.input_tokens, u.output_tokens, u.total_tokens)
```

`bot.usage` covers every AI call on the client. Per-call numbers are on each
result object (`ExtractResult`, `VerifyResult`, `LocateResult`) and on every
`StepResult` from `ai()`. A call's spend is `input_tokens + output_tokens` —
`output_tokens` already includes thinking, so never add `thinking_tokens`
again. Field-by-field details are in the
[Method Reference](/reference/methods#usage).

The same totals print after every CLI task, appear in the HTML report
header, and are in the `usage` object of `--output-format json`. For a
per-call breakdown while tuning, `QIRA_ENGINE_TRACE=<dir>` writes one JSONL
record per model call.

See also: [Configuration](/advanced/configuration) (every knob and its env
var) · [AI Tasks & Custom Tools](/advanced/ai-tasks)
