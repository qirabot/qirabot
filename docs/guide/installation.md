---
title: Installation
description: Install the Qirabot Python SDK and CLI with uv — one-line installer or uv tool install. Includes per-backend extras for browser, desktop, and Appium, plus troubleshooting.
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

## As a library

To import `qirabot` in your own tests, install it into your project's
environment rather than a tool environment. Requires Python 3.10+; `uv venv`
downloads one if the machine has none:

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install "qirabot[browser]"
qirabot install-browser          # or: playwright install chromium
```

## Extras per backend

The core package attaches to the Playwright / Selenium / Appium / pyautogui
session you already run; the frameworks themselves live in extras. Each
platform page carries the exact command for its backend:

| Extra | Backend |
| --- | --- |
| `browser` | [Playwright — managed browser](/backends/browser) |
| `desktop` | [pyautogui — whole desktop, any OS](/backends/desktop) |
| `appium` | [Appium — Android / iOS via a server; device clouds](/frameworks/appium) |
| `all` | everything above |
| none | [Android](/backends/android) (adb), [iOS](/backends/ios) (WDA), [Windows](/backends/windows-games) single-window, and [Selenium](/frameworks/selenium) (bring your own driver) |

In a tool environment, name every extra you want in one command —
`uv tool install --force "qirabot[browser,desktop]"`. uv replaces the
environment with exactly what you asked for, so adding `[desktop]` on its own
would drop the installer's `[browser]`. When in doubt, `qirabot doctor` prints
the right line for the environment you are in.

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
- No uv on the machine? `pip install "qirabot[browser]"` into an activated
  virtualenv works the same. Installing into the system Python does not:
  Debian and Ubuntu block it per PEP 668
  (`error: externally-managed-environment`).
- On a fresh Linux box, run `sudo playwright install-deps chromium` once. The
  Chromium download doesn't include the system libraries it links against
  (`error while loading shared libraries: libnspr4.so ...`).

## Next steps

- [Quick Start](/guide/quickstart): authenticate to Google Cloud and run your first task
- [CLI Reference](/guide/cli): run natural-language tasks without writing code
