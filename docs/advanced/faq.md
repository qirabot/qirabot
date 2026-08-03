---
title: FAQ — Common Questions about Qirabot
description: Google Cloud credential setup, which calls invoke the model, why recordings come out black, headless fallback, long waits between steps, and other frequently asked questions.
---

# FAQ

## What credentials do I need?

Google Cloud Application Default Credentials (ADC) — no Qirabot account or
API key. The decision engine runs locally inside the SDK and calls Google
Vertex AI in your own project: set `GOOGLE_APPLICATION_CREDENTIALS` to a
service-account JSON, or run `gcloud auth application-default login` once
(on GCE the metadata server is used automatically). Pick the model with
`Qirabot(model="{provider}/{model}")` or `QIRA_MODEL` — see
[Configuration](/advanced/configuration#model-language). `qirabot doctor`
verifies the setup.

## Which calls invoke the model, and which don't?

Calls that invoke the AI send a screenshot to your Vertex AI endpoint (and
consume tokens there): `ai()`, `extract`, `verify`, `wait_for`, and the
AI-located actions (`click`, `type_text`, `double_click` with an element
description). Direct actions never touch the AI and cost nothing:
`navigate`, `go_back`, `close_tab`, `scroll`, `press_key`, `screenshot`,
`launch_app`, `type_text` with an empty locate, and `mouse_up` without a
locate. The [API reference](/reference/api) marks these "no AI". Model
usage is billed by Google Cloud on your project — token counts are on
every result object.

## What data leaves my machine?

Screenshots, your instruction text, and step metadata — sent directly from
your machine to the model endpoint you configured (Google Vertex AI, in
your own Google Cloud project). No Qirabot server is involved. Code,
cookies, and credentials stay local; actions execute on your machine. Full
details in [Data & Privacy](/reference/privacy).

## Why is my recording black?

- **Windows, `record_window=True`**: `gdigrab` produces black frames for
  minimized or GPU-composited (fullscreen-exclusive game) windows — keep the
  window visible, or record the full screen for games.
- **macOS**: grant your terminal/IDE the "Screen Recording" permission.

Recording is best-effort: a missing ffmpeg or denied permission warns and
never fails the task — check `recording.ffmpeg.log` in the run dir. See
[Reports & Recording](/advanced/reports).

## Why did the browser start headless?

On a display-less box (no `DISPLAY`), `bot.open()` and the CLI automatically
fall back to headless, with a warning. Pass `--headless` explicitly to make
it unconditional.

## I got `MissingDependencyError` — what now?

An optional backend dependency isn't installed. The error message contains
the exact `pip install "qirabot[<extra>]"` to run; the extras are listed in
[Installation](/guide/installation).

## My script sleeps between steps — will the run time out?

No. The engine runs locally in your process — there is no server session
to keep alive, so long waits between `bot.*` calls are safe. Details in
[Configuration](/advanced/configuration#run-lifecycle).

## Can I type Chinese or emoji on Android?

Yes — `bot.type_text(...)` works out of the box. Text beyond ASCII goes
through the bundled ADBKeyboard IME, installed on demand and switched back
afterwards. See [Android](/backends/android).

## Do I have to rewrite my Playwright / Selenium / Appium suite?

No. Pass your existing `page` or `driver` as the target and add AI steps
only where selectors hurt — see the integration guides for
[Playwright](/frameworks/playwright), [Selenium](/frameworks/selenium),
[Appium](/frameworks/appium), and [pytest](/frameworks/pytest).

## I'm coming from Airtest / qirabot 1.x

The built-in device backends are drop-in replacements
(`connect_device(...)` → `AdbDevice` / `WdaClient` / `Window`), and a
reference adapter keeps old scripts running unchanged. See
[Migrating from Airtest](/backends/custom-adapters#migrating-from-airtest-qirabot-1-x).

## I'm coming from qirabot 2.x (cloud engine)

v3 replaced the cloud decision engine with a local one: no account, no
`QIRA_API_KEY`, no `qirabot login` — configure Google Cloud ADC and a
model instead. If `QIRA_API_KEY` is set but no model is configured,
`Qirabot()` raises a clear error with migration steps at construction. To
keep the old cloud behavior, pin `pip install "qirabot<3"`.
