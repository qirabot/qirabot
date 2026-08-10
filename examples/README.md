# Qirabot examples

Four ways to use the SDK — pick by how your code is shaped.

## 1. Standalone scripts

`bot.open()` launches its own browser. No pytest, no fixtures, no webdriver
setup. Build scraping / RPA / agent scripts and run with `python`.

- [automation/](automation/) — `bot.open()`, `bot.ai()`, scraping, CDP connect

## 2. Drive a game

Games have no DOM and no accessibility tree — Qirabot drives them purely by
what's on screen. On Windows, bind the renderer window by HWND; on a real
iPhone, bind an Appium session. Mix deterministic steps for the known launch /
splash flow with `bot.ai()` for open-ended play or UI audits.

- [game/](game/) — Unity / Unreal / native Windows games via `Windows:///<hwnd>`,
  plus the iOS MMORPG script behind the [demo video](https://qirabot.com/docs/demos.html#mhxy_zero_to_15)

## 3. Bolt onto a framework you already use

Add AI where selectors are fragile — visual assertions, fuzzy element
descriptions, unstructured extraction — without rewriting the rest of your
script. Works inside pytest suites or as plain scripts. Organized by the
framework you bring:

- [playwright/](playwright/) — Playwright `page` (pytest-playwright or your own)
- [selenium/](selenium/) — your own `webdriver.Chrome()`
- [appium/](appium/) — Android / iOS via `webdriver.Remote`
- [adb/](adb/) — Android direct over adb (built in, zero dependencies)
- [ios/](ios/) — iOS direct via WebDriverAgent (built in, zero dependencies)
- [windows/](windows/) — one Windows window, game-readable scancode input (built in)
- [desktop/](desktop/) — native apps via pyautogui
- [airtest/](airtest/) — airtest devices (Android/iOS/Windows) via a copy-in adapter (`register_adapter`)

## 4. Run desktop scripts on a dedicated machine

Run desktop (pyautogui) automation on a separate, always-on machine (e.g. a
Windows VM) so screenshots never capture your editor and the bot never steals
your local mouse. Write/test locally, then POST the script to the remote runner.

- [runner/](runner/) — tiny HTTP runner + dedicated-machine deployment guide (Windows / macOS)

## Setup

The v3 decision engine runs locally against your own Vertex AI endpoint — auth
is Google Cloud ADC; no Qirabot account, no per-step billing:

```bash
gcloud auth application-default login          # once; or set GOOGLE_APPLICATION_CREDENTIALS
export QIRA_VERTEX_PROJECT="my-gcp-project"    # if your ADC doesn't carry a project id
```

Pick a model with `QIRA_MODEL` or `Qirabot(model="{provider}/{model}")` —
provider one of `gemini-vertex` / `gemini`; the
default is `gemini-vertex/gemini-3.6-flash`.

Install instructions are at the top of each script; the larger subdirectories
also have a README with setup details.
