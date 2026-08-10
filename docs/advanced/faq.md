---
title: FAQ — Common Questions about Qirabot
description: Google Cloud credential setup, which calls invoke the model, why recordings come out black, headless fallback, long waits between steps, and other frequently asked questions.
---

# FAQ

## What credentials do I need?

Google Cloud Application Default Credentials (ADC). There is no Qirabot
account and no Qirabot API key. The decision engine runs locally inside
the SDK and calls Google Vertex AI in your own project: set
`GOOGLE_APPLICATION_CREDENTIALS` to a service-account JSON, or run
`gcloud auth application-default login` once (on GCE the metadata server
is used automatically). Pick the model with
`Qirabot(model="{provider}/{model}")` or `QIRA_MODEL`; see
[Configuration](/advanced/configuration#model-language). `qirabot doctor`
verifies the setup.

## Which calls invoke the model, and which don't?

Calls that invoke the AI send a screenshot to your Vertex AI endpoint (and
consume tokens there): `ai()`, `extract`, `verify`, `wait_for`, and the
AI-located actions (`click`, `type_text`, `double_click` with an element
description). Direct actions never invoke the model and consume no tokens:
`navigate`, `go_back`, `close_tab`, `scroll`, `press_key`, `screenshot`,
`launch_app`, `type_text` with an empty locate, and `mouse_up` without a
locate. The [API reference](/reference/api) marks these "no AI". Model
usage is billed by Google Cloud on your project; token counts are on
every result object.

## How do I bring the token bill down?

The biggest fixed cost per step is the tool schemas, not the screenshot, so
`exclude_tools` is usually the first win — followed by dropping
`media_resolution` from `high` to `medium` and replacing known flow segments
with deterministic steps instead of `ai()`. Note that `screenshot_quality`
is *not* a cost lever: image tokens depend only on the resolution tier. Full
breakdown in [Controlling Cost](/advanced/cost).

## I upgraded from v2 and it won't start

v3 refuses to run while a v2-era `QIRA_API_KEY` is in the environment,
rather than silently switching billing to a Google Cloud project. Unset
`QIRA_API_KEY` and `QIRA_BASE_URL` (including in `.env` and CI secrets). Full
path in [Upgrading from v2](/guide/migration-v3).

## What data leaves my machine?

Screenshots, your instruction text, and step metadata. They are sent
directly from your machine to the model endpoint you configured (Google
Vertex AI, in your own Google Cloud project). No Qirabot server is
involved. Code,
cookies, and credentials stay local; actions execute on your machine. Full
details in [Data & Privacy](/reference/privacy).

## Why is my recording black?

- **Windows, `record_window=True`**: the default mode crops a desktop grab to
  the window, so games record fine — but a minimized window records nothing.
  Keep it visible and unmoved. With `QIRA_RECORD_WINDOW_NATIVE=1` the older
  `gdigrab` mode also goes black on GPU-composited (game) windows.
- **macOS**: grant your terminal/IDE the "Screen Recording" permission.

Recording is best-effort: a missing ffmpeg or denied permission logs a
warning and does not fail the task. Check `recording.ffmpeg.log` in the
run dir. See [Reports & Recording](/advanced/reports).

## Why did the browser start headless?

On a display-less box (no `DISPLAY`), `bot.open()` and the CLI automatically
fall back to headless, with a warning. Pass `--headless` explicitly to make
it unconditional.

## I got `MissingDependencyError` — what now?

An optional backend dependency isn't installed. The error message contains
the exact install command for the environment qirabot is running in — a
`uv tool install --force` inside a uv tool environment, a plain install into
a project environment otherwise. The extras are listed in
[Installation](/guide/installation).

## My script sleeps between steps — will the run time out?

No. The engine runs locally in your process, and there is no server
session to keep alive, so long waits between `bot.*` calls are safe. Details in
[Configuration](/advanced/configuration#run-lifecycle).

## Can I type Chinese or emoji on Android?

Yes. `bot.type_text(...)` works out of the box. Text beyond ASCII goes
through the bundled ADBKeyboard IME, installed on demand and switched back
afterwards. See [Android](/backends/android).

## Do I have to rewrite my Playwright / Selenium / Appium suite?

No. Pass your existing `page` or `driver` as the target and add AI steps
only where selectors hurt. See the integration guides for
[Playwright](/frameworks/playwright), [Selenium](/frameworks/selenium),
[Appium](/frameworks/appium), and [pytest](/frameworks/pytest).

## I'm coming from Airtest

The built-in device backends are drop-in replacements
(`connect_device(...)` → `AdbDevice` / `WdaClient` / `Window`), and a
reference adapter keeps your airtest scripts running unchanged. See
[Migrating from Airtest](/backends/custom-adapters#migrating-from-airtest).
