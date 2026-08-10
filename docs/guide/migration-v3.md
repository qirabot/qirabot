---
title: Upgrading from v2 — the Local Decision Engine
description: What changed when Qirabot's decision engine moved from the cloud into the SDK — replacing QIRA_API_KEY with Google Cloud credentials, picking a model, the removed claude-vertex provider, and how to pin v2 instead.
---

# Upgrading from v2

In v3 the decision engine moved out of Qirabot's cloud and into the SDK
process. Screenshots now go directly from your machine to a vision model on
your own Google Vertex AI project. There is no Qirabot account, no Qirabot
API key, and no server in the loop.

**The automation API did not change.** `bot.ai()`, `bot.click()` /
`type_text()` / `extract()` / `verify()` / `locate()`, `knowledge=`,
`custom_tools=`, `on_step=`, `thinking_level=`, every backend
(browser / Android / iOS / desktop / Windows window), HTML reports,
recording, and the overlay all behave as before. What changed is how the
SDK authenticates and which model it calls.

## The error you probably landed here for

```
Your Google Cloud setup works and Qirabot v3 is ready to use it — but a
v2-era QIRA_API_KEY is still set …
```

v3 refuses to start while a v2 `QIRA_API_KEY` is in the environment, rather
than silently moving your billing to a Google Cloud project. Clear the v2
variables and the error goes away:

```bash
unset QIRA_API_KEY QIRA_BASE_URL     # also remove them from .env / CI secrets
```

Setting `model=` (or `QIRA_MODEL`) also counts as acknowledging the switch
and disarms the guard.

## Migrating in three steps

**1. Authenticate to Google Cloud.** v3 uses standard Application Default
Credentials:

```bash
gcloud auth application-default login
```

A service-account JSON via `GOOGLE_APPLICATION_CREDENTIALS` works too, as
does running on GCE with the metadata server. If you'd rather not set up
gcloud at all, both key-based paths are available instead: a Vertex AI API
key (`QIRA_VERTEX_API_KEY`) or an AI Studio key for the `gemini` provider
(`QIRA_GEMINI_API_KEY`). See [Configuration](/advanced/configuration).

**2. Drop the v2 configuration.**

| v2 | v3 |
|---|---|
| `QIRA_API_KEY` | removed — Google Cloud ADC, or a Vertex / AI Studio API key |
| `QIRA_BASE_URL` | removed — there is no Qirabot server to point at |
| `Qirabot(model="fast")` and other cloud aliases | `model="{provider}/{model}"`; the alias concept is gone |
| server-side task ids | `bot.task_id` is local, of the form `local-<8 hex>` |

**3. Pick a model (optional).** Unset, v3 uses
`gemini-vertex/gemini-3.6-flash` on the project your credentials already
carry. Override with `Qirabot(model=...)` or `QIRA_MODEL`.

Then verify the whole environment at once:

```bash
qirabot doctor      # Python, ADC + project, backend deps, leftover v2 vars
qirabot models      # providers, default models, which auth resolves
```

## Provider changes

v3.0 shipped `claude-vertex` and `gemini-vertex`. **`claude-vertex` was
removed in v3.1** — `gemini-vertex` and `gemini` are the two providers, and
a `model="claude-vertex/..."` value now fails at construction with the
unknown-provider hint. If you still need Claude on Vertex, pin
`qirabot<3.1`.

## Billing and rate limits

Model calls are billed by Google Cloud on your project at that model's
rates; Qirabot no longer charges per step. Two consequences for existing
code:

- `InsufficientBalanceError`, `QirabotConnectionError`, `TaskTerminatedError`
  and `RateLimitError` were **removed in v3.2** — there is no Qirabot
  billing, connection, or server-side task state left for them to describe.
  (v3.0 and v3.1 still exported them as never-raised stubs.) Delete the
  `except` clauses written against them, or widen them to `QirabotError`;
  see [Error Handling](/advanced/error-handling).
- Quota is now your project's Vertex AI quota. Rate limits are retried
  inside the provider layer and only surface as `ActionError` if they
  persist. For keeping the bill down, see
  [Controlling Cost](/advanced/cost).

## Staying on v2

The Qirabot cloud backend that v2 talks to is gone, so v2 is not a
supported path forward — but the pin still exists if you need time:

```bash
uv pip install "qirabot<3"
```

## Next steps

- [Quick Start](/guide/quickstart) — the v3 first run
- [Configuration](/advanced/configuration) — credentials, providers, every knob
- [Controlling Cost](/advanced/cost) — what a step costs and which levers move it
