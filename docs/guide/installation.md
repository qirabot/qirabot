---
title: Installation
description: Install the Qirabot Python SDK and CLI — one-line installer, uv, or pip. Includes per-backend extras for browser, desktop, and Appium, plus troubleshooting.
---

# Installation

The one-line installer sets up [uv](https://docs.astral.sh/uv/), qirabot (in
an isolated environment that never touches your system Python), and Chromium.
It does not require a pre-installed Python:

::: code-group

```bash [macOS / Linux]
curl -LsSf https://qirabot.com/install | sh
```

```powershell [Windows]
powershell -ExecutionPolicy ByPass -c "irm https://qirabot.com/install.ps1 | iex"
```

:::

If you already have uv, you can get the same result by hand:

```bash
uv tool install "qirabot[browser]" && qirabot install-browser
```

To drive a device instead of a browser, no extras are needed: the Android
(adb), iOS (WDA), and Windows single-window backends are built into the core
package. The install is:

```bash
uv tool install qirabot        # Android + iOS + Windows window; zero extras
```

## pip / virtualenv

Requires Python 3.10+. Use a virtualenv; Debian and Ubuntu block installs
into the system Python per PEP 668:

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install "qirabot[browser]"
qirabot install-browser          # or: playwright install chromium
```

To use qirabot as a library (importing `qirabot` in your own tests), install
it into your project's environment instead of a tool environment:
`uv pip install "qirabot[browser]"`, or the pip lines above.

## Extras per backend

The core package attaches to the Playwright / Selenium / Appium / pyautogui
session you already run. The frameworks themselves live in extras. Install
the one that matches your setup, or none if it is already in your environment:

```bash
python -m pip install "qirabot[browser]"   # Playwright (managed browser)
python -m pip install "qirabot[desktop]"   # pyautogui (whole-desktop, any OS)
python -m pip install "qirabot[appium]"    # Appium (Android / iOS via a server; device clouds)
python -m pip install "qirabot[all]"       # everything above

python -m pip install qirabot selenium     # Selenium is not an extra — bring your own driver
```

All extras install cleanly together in one environment; since 2.0 nothing
pins numpy/opencv.

## Verify your environment

```bash
qirabot doctor
```

`doctor` reports the Python version, whether Google Cloud credentials (ADC)
resolve and to which project, and each backend's dependencies, along with
the exact command to fix anything missing. If you have no credentials yet,
run the [Quick Start](/guide/quickstart)'s first command
(`gcloud auth application-default login`).

## Troubleshooting

- The one-line installer is also served directly from the GitHub repo:
  `curl -LsSf https://raw.githubusercontent.com/qirabot/qirabot/main/scripts/install.sh | sh`
- `error: externally-managed-environment` means you are installing into the
  system Python (PEP 668). Use the uv path above, or create and activate a
  virtualenv.
- On a fresh Linux box, run `sudo playwright install-deps chromium` once. The
  Chromium download doesn't include the system libraries it links against
  (`error while loading shared libraries: libnspr4.so ...`).
- On a display-less box (headless server / VM, no `DISPLAY`), a visible
  browser window can't open. `bot.open()` and the CLI detect that and
  automatically run headless, with a warning.

## Next steps

- [Quick Start](/guide/quickstart): authenticate to Google Cloud and run your first task
- [CLI Reference](/guide/cli): run natural-language tasks without writing code
