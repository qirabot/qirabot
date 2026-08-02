---
title: Data & Privacy
description: Exactly what Qirabot sends to your own Vertex AI endpoint (screenshots, instructions, step metadata), what never leaves your machine (code, cookies, credentials), and the local-only report files. No Qirabot service is involved.
---

# Data & Privacy

Qirabot's decision engine runs locally inside the SDK: the model needs to
see the screen, and nothing else. Screenshots go directly from your machine
to the Vertex AI endpoint of your own Google Cloud project. The `qirabot`
package makes no network calls to any Qirabot service — there is no
account, no API key, and no server-side task store. This page states
exactly what crosses the wire.

## What is sent to your model endpoint

Each AI step sends to the Vertex AI model you configured, under your Google
Cloud project:

- a **screenshot** of the bound target (JPEG quality 80 by default —
  `screenshot_format` / `screenshot_quality` in
  [Configuration](/advanced/configuration)),
- your **instruction text** (the natural-language description or task),
- **step metadata** (action type, parameters, timing).

Content the model extracts from the screen (`extract()` results, `ai()`
outputs) is produced by that same endpoint and returned to your process.
No other party receives it.

## What never leaves your machine

- **Your code.** The model returns coordinates and decisions; actions
  execute locally through your framework or adapter.
- **Cookies, credentials, session state.** Qirabot drives your browser or
  device; it doesn't read or transmit their storage.
- **Custom tools.** Functions passed via `custom_tools` run locally — your
  endpoints, tokens, and databases are never seen by the model endpoint;
  only the tool's string return value is fed back to the model. See
  [AI Tasks & Custom Tools](/advanced/ai-tasks).

## What stays local

All run data — name, status, steps, and step screenshots — stays on local
disk. The [HTML report](/advanced/reports) (`report.html`, full-resolution
`screenshots/`, `recording.mp4`) is written to `./qira_runs/` on your
machine, is fully self-contained, and makes no network calls. Disable it
with `report=False`. With `QIRA_ENGINE_TRACE=<dir>` set (a debugging aid),
the engine additionally writes one JSONL record per model call plus the
screenshots into that local directory.

## Transport

All model traffic is HTTPS to your configured Vertex AI endpoint,
authenticated with your Google Cloud credentials (Application Default
Credentials). There is no other network destination.
