# Qirabot Python SDK

English | [简体中文](README.zh.md)

Cross-platform GUI automation, driven by multimodal AI vision. Drive browsers, mobile apps, full desktops, and games through pixels — no DOM, no selectors — reaching what frameworks like Playwright, Selenium, and Appium cannot.

Run it standalone (`bot.open()` launches a browser for you; Android / iOS / Windows-window backends are built in with zero extra dependencies), bolt it onto your existing Playwright / Selenium / Appium / pyautogui session, drop it into a pytest suite, or bind by HWND to drive a Unity / Unreal / native desktop game. Same API across all of them.

The decision engine runs locally inside the SDK: each screenshot goes directly from your machine to a vision model you configure on Google Vertex AI, authenticated with your own Google Cloud credentials. There is no Qirabot server in the loop — no account, no API key, no per-step billing.

**📖 Full documentation: [qirabot.com/docs](https://qirabot.com/docs/)** ([中文](https://qirabot.com/docs/zh/))

## See it work

https://github.com/user-attachments/assets/649ea80c-63e7-4c85-9ee8-3c8fe17e5ef4

**Play an MMORPG from zero to level 15, hands-free** — iOS real device.
The entire task prompt is one sentence: *"This is Fantasy Westward Journey
mobile. Create a character, then complete the new-player flow; skip whatever
can be skipped."* Highlights cut from a single unedited run:
[full 5:50 video](https://qirabot.com/#demos) ·
[script](examples/game/ios_appium_mmorpg.py)

More real, unedited runs — the AI sees only pixels. Click a poster to watch
([all demos →](https://qirabot.com/#demos)):

<table>
  <tr>
    <td align="center" width="33%">
      <a href="https://qirabot.com/#demos"><img src="https://assets.qirabot.com/demos/afk_journey_tutorial.poster.webp" alt="Clear AFK Journey's tutorial and reach the open world"></a>
      <br><b>Clear AFK Journey's tutorial and reach the open world</b> — iOS real device
    </td>
    <td align="center" width="33%">
      <a href="https://qirabot.com/#demos"><img src="https://assets.qirabot.com/demos/lichess_play_chess.poster.webp" alt="Play chess on lichess.org"></a>
      <br><b>Play chess on lichess.org</b> — Android real device
    </td>
    <td align="center" width="33%">
      <a href="https://qirabot.com/#demos"><img src="https://assets.qirabot.com/demos/tile_match_game.poster.webp" alt="Beat a fruit tile-match game on its own"></a>
      <br><b>Beat a fruit tile-match game on its own</b> — Android real device
    </td>
  </tr>
</table>

## Installation

One line — installs [uv](https://docs.astral.sh/uv/), qirabot (isolated, never
touches your system Python), and Chromium. No pre-installed Python required:

```bash
# macOS / Linux
curl -LsSf https://qirabot.com/install | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://qirabot.com/install.ps1 | iex"
```

Driving a device instead of a browser? The Android (adb), iOS (WDA), and
Windows single-window backends are built into the core package:

```bash
uv tool install qirabot        # Android + iOS + Windows window; zero extras
```

pip, virtualenvs, per-framework extras, and troubleshooting:
[Installation guide](https://qirabot.com/docs/guide/installation.html).
Whichever path you took, `qirabot doctor` reports what is installed, what is
missing (with the exact fix), and whether your Google Cloud credentials resolve.

Upgrading from v2? The cloud backend (accounts, API keys) is gone in v3 — see
[CHANGELOG.md](CHANGELOG.md) for the migration table, or pin
`pip install "qirabot<3"` to stay on the v2 cloud behavior.

## Quick Start

Qirabot calls a vision model on Google Vertex AI with your own Google Cloud
credentials. Set up Application Default Credentials (ADC) once:

```bash
gcloud auth application-default login
# or, for service accounts / CI:
# export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

Optionally pick a model as `{provider}/{model}` — provider is one of
`claude-vertex`, `gemini-vertex`; the default is
`gemini-vertex/gemini-3.6-flash`. Set the Google Cloud project if your
credentials don't already carry one:

```bash
export QIRA_MODEL="gemini-vertex/gemini-3.6-flash"   # or Qirabot(model=...)
export QIRA_VERTEX_PROJECT="my-gcp-project"                # or Qirabot(vertex_project=...)
```

`qirabot models` lists the providers and verifies your credentials.
Then hand the AI a task. Real, unedited output:

```text
$ qirabot browser "Search for SpaceX and get the first sentence of the article" --url wikipedia.org
Run: local-6237d4ff
[1/20] type_text  ← "SpaceX"
        └ Type 'SpaceX' into the Wikipedia search bar and press enter to search.
Done: Space Exploration Technologies Corp., doing business as SpaceX, is an
      American spaceflight, telecommunications, and artificial intelligence
      company headquartered at the Starbase development site in Starbase, Texas.
```

Every run writes an HTML report with per-step screenshots; `--record`
captures a video of the whole run.

For sites that need an account, log in once by hand — no site credentials in
your scripts — and every later run that reuses the profile starts already
signed in:

```bash
qirabot open-browser --user-data-dir ~/.automation --url news.ycombinator.com/login
# log in in the window, close it, then:
qirabot browser "Upvote the top story about Rust" --user-data-dir ~/.automation
```

<!-- TODO: real-world CLI transcript section here, mirroring README.zh.md's
     "实战场景：社交媒体运营" (two runs sharing --user-data-dir: first `qirabot
     open-browser` for the manual login, second reuses the session unattended).
     Needs a real English-language run — do not translate the Chinese
     transcript. -->

## Python SDK

The CLI is powered by the same engine. Call `bot.ai()` from Python and the AI
likewise looks at the screen, decides the next action, and loops until the
task is done — except the result lands directly in your code, with an
`on_step` callback streaming each action as it happens:

```python
from qirabot import Qirabot, StepResult

bot = Qirabot()
page = bot.open("https://www.wikipedia.org")

def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params}")

result = bot.ai(page, "Search for SpaceX and get the first sentence of the article", on_step=on_step)
print(f"Success: {result.success}")
print(f"Result: {result.output}")

bot.close()
```

Prefer to drive each step yourself? The same natural-language targeting works
as single-step calls — `bot.click(page, "Login button")`,
`bot.extract(...)`, `bot.verify(...)` — with your code in control. Need just
the coordinates? `x, y = bot.locate(page, "the OK button")` resolves an
element without acting, so you can feed them to your own framework calls.

## Bolt onto your existing stack

No rewrite: pass your existing `page` / `driver` / device object and mix AI
steps with the selectors you already have. Add AI where selectors hurt —
visual assertions, dynamic widgets, and flows too tedious to script:

```python
import pytest
from qirabot import Qirabot

@pytest.fixture(scope="session")
def bot():
    with Qirabot(task_name="test-checkout") as bot:   # one task per run
        yield bot

def test_checkout(page, bot):     # `page` is your pytest-playwright fixture
    page.goto("https://shop.example.com")
    page.fill("#username", "test_user")             # your selectors, as-is
    page.click("#login-btn")

    # Visual assertion — survives markup rewrites and CSS refactors
    assert bot.verify(page, "the product grid shows items with prices and no error banner")

    # One line replaces a page of brittle selector steps
    result = bot.ai(page, "Complete checkout, name John Doe zip 10001", max_steps=8)
    assert result.success
```

Works the same for Selenium, Appium, pyautogui, and the built-in device
backends (`AdbDevice`, `WdaClient`, `Window`) — and anything else via a
7-primitive [custom adapter](https://qirabot.com/docs/backends/custom-adapters.html).

## Use with AI agents

Qirabot ships a pre-built [skill](https://qirabot.com/docs/guide/agents.html)
following the [Agent Skills open standard](https://agentskills.io), loadable
by Claude Code, Codex, Cursor, and other compatible agents. The skill bundles
a preflight environment check, a condensed API reference aligned with the SDK
version, and per-platform starter templates. Given a natural-language
automation goal, the agent validates the environment, picks the execution
path — the CLI for one-shot tasks, an SDK script when the flow needs
branching or returned values — and verifies the run's outcome.

Any Agent-Skills-compatible tool can install the copy bundled in the pip
package, version-matched to the installed SDK:

```bash
qirabot skill install agents        # or claude / codex / cursor / --dir <path>
```

Claude Code users should prefer the plugin marketplace (auto-updates):

```text
/plugin marketplace add qirabot/claude-plugins
/plugin install qirabot@qirabot
```

The [skills CLI](https://github.com/vercel-labs/skills) also works,
installing from the repository's `main` branch:

```bash
npx skills add qirabot/qirabot-python
```

The skill's reference and templates are drift-tested against the live SDK in
CI (`tests/test_skill.py`). Details: [plugins/qirabot/README.md](plugins/qirabot/README.md).

## Domain knowledge: teach the AI your rules

The model knows how to drive a UI — not your game's item names or your team's
business terms. Mount reference text for the task and the AI consults it at
every step. From the CLI, `-k` takes a file and repeats, 32KB total:

```bash
qirabot browser "Buy 10 stamina potions in the shop" -k game-rules.md -k gm-policy.md
```

From Python, `knowledge` takes literal text, a UTF-8 file, or a list mixing both:

```python
result = bot.ai(
    device,
    "Complete every daily quest",
    knowledge=[Path("game-rules.md"), "GM commands may be used once per match"],
)
```

Knowledge is mounted per call: the next `bot.ai()` starts clean, so each stage
of a long flow carries only what it needs. Two deliberate limits: no URLs —
fetch remote sources yourself, so auth and failures stay in your code — and
knowledge *guides* decisions; hard rules like "once per match" belong in
custom-tool code (next section), where they can actually be enforced.

## Custom tools: let the AI call your code

Mid-task, the AI isn't limited to clicking and typing. `custom_tools`
registers plain Python functions the model can invoke as it works — hit an
internal API, query a database, fetch an OTP from your mail server, seed test
data, or pause for a human at a CAPTCHA. Name, description, and parameters are
introspected from the function itself:

```python
def gm_command(command: str) -> str:
    """Send a command to the game's GM backend and return its reply.
    Available commands: add_energy <amount>, add_gold <amount>"""
    return requests.post(GM_URL, json={"cmd": command}, timeout=10).text

result = bot.ai(
    device,
    "Complete every daily quest. If an out-of-energy popup appears, "
    "use gm_command to add 100 energy and continue",
    custom_tools=[gm_command],
)
```

The tool runs locally in your process, like everything else in v3 — the model
sees only the tool's name, description, parameters, and return value, never
your endpoints or credentials — and that return value becomes the model's
next observation. One instruction now spans systems that used to take a page of
glue code: UI steps, backend calls, and human handoffs in a single flow.
Details (schemas, error handling, pruning built-in tools):
[AI Tasks & Custom Tools](https://qirabot.com/docs/advanced/ai-tasks.html).
Runnable examples: [custom_tool_gm.py](examples/game/custom_tool_gm.py) ·
[06_human_in_the_loop.py](examples/automation/06_human_in_the_loop.py).

## Progress overlay

Every CLI task command shows a small always-on-top window in the screen's
bottom-right corner: the running instruction, each step's action and
reasoning, and the final ✓/✗ outcome. The window is **excluded from screen
capture** and click-through — it never appears in the bot's own screenshots
and never intercepts a click meant for the app below. Turn it off with
`--no-overlay`; in the SDK it's one flag:

```python
bot = Qirabot(overlay=True)   # every bot.ai() run reports to the window
```

When a task drives the machine's **real mouse and keyboard** (the desktop
backends), a slow-breathing amber glow lines the screen edges — the "machine
is being controlled, hands off" signal — and **holding ESC for about a
second aborts the run**: the bot releases every key it was holding and the
task is recorded as **cancelled**, not failed. Remote-protocol targets
(browser, Android, iOS) light neither: your mouse stays yours there.

A standalone `Overlay` also displays your own script's phases alongside the
bot's steps. Details — abort semantics (`user_abort`, `clear_user_abort()`),
the standalone API, platform notes and limits:
[Progress Overlay & Kill Switch](https://qirabot.com/docs/advanced/overlay.html).
Runnable example: [overlay_progress.py](examples/desktop/overlay_progress.py).

## Documentation

| Topic | |
|---|---|
| Getting started | [Installation](https://qirabot.com/docs/guide/installation.html) · [Quick Start](https://qirabot.com/docs/guide/quickstart.html) · [CLI Reference](https://qirabot.com/docs/guide/cli.html) |
| Platforms | [Browser](https://qirabot.com/docs/backends/browser.html) · [Android (adb, no Appium)](https://qirabot.com/docs/backends/android.html) · [iOS (WDA, no Appium)](https://qirabot.com/docs/backends/ios.html) · [Windows & Games (DirectInput)](https://qirabot.com/docs/backends/windows-games.html) · [Desktop](https://qirabot.com/docs/backends/desktop.html) · [Custom Adapters](https://qirabot.com/docs/backends/custom-adapters.html) |
| Integrations | [Playwright](https://qirabot.com/docs/frameworks/playwright.html) · [Selenium](https://qirabot.com/docs/frameworks/selenium.html) · [Appium](https://qirabot.com/docs/frameworks/appium.html) · [pytest](https://qirabot.com/docs/frameworks/pytest.html) |
| Advanced | [AI Tasks & Custom Tools](https://qirabot.com/docs/advanced/ai-tasks.html) · [Progress Overlay & Kill Switch](https://qirabot.com/docs/advanced/overlay.html) · [Reports & Recording](https://qirabot.com/docs/advanced/reports.html) · [Configuration](https://qirabot.com/docs/advanced/configuration.html) · [Error Handling](https://qirabot.com/docs/advanced/error-handling.html) |
| Reference | [API — Actions & Platform Matrix](https://qirabot.com/docs/reference/api.html) |

## Examples

Runnable examples live in [examples/](examples/), in three styles:

- **Bolt onto your existing tests (pytest)** — [playwright/](examples/playwright/),
  [selenium/](examples/selenium/), [appium/](examples/appium/),
  [desktop/](examples/desktop/)
- **Standalone automation (plain scripts)** — scraping / RPA / agents:
  [automation/](examples/automation/)
- **Drive a game** — Windows desktop games (bind by HWND) and the iOS
  MMORPG script behind the [demo video](https://qirabot.com/#demos):
  [game/](examples/game/)

See [examples/README.md](examples/README.md) for which to pick.

## License

MIT
