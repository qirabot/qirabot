# Drive a game with Qirabot

DOM-based automation (Playwright / Selenium / Appium locators) can't reach a
game's UI — there's no accessibility tree and the renderer is on the GPU.
Qirabot drives the game purely by what's on screen: on Windows bind the
renderer window by HWND; on a phone bind the device session. Mix
deterministic steps (for known splash / launcher UI) with `bot.ai()` (for
open-ended play or audits).

| Example | Platform | What it does |
|---|---|---|
| [windows_unity_game.py](windows_unity_game.py) | Windows — Unity (default) / Unreal / native | Launch the game, wait for splash → in-game, then audit the character & inventory menus with `bot.ai()`. |
| [ios_appium_mmorpg.py](ios_appium_mmorpg.py) | iOS real device (Appium XCUITest) | The script behind the ["zero to level 15" demo video](https://qirabot.com/#demos): one `bot.ai()` task creates an MMORPG character and clears the whole new-player flow, recording the screen throughout. Setup/run instructions are in the script docstring. |

The rest of this README covers the Windows example; the iOS example is
self-contained (prerequisites and run steps in its docstring).

## Install

```bash
python -m pip install qirabot   # the Windows window backend is built in
```

`pywin32` provides the `win32gui` calls used to find the game's renderer
window by class name (Unity → `UnityWndClass`, Unreal → `UnrealWindow`, etc.).

## Run

Auth is Google Cloud ADC — run `gcloud auth application-default login` once
(or set `GOOGLE_APPLICATION_CREDENTIALS`); pick a model via `QIRA_MODEL` or
`Qirabot(model=...)`.

```cmd
copy .env.example .env       :: then edit QIRA_GAME_EXE (+ QIRA_MODEL / QIRA_VERTEX_PROJECT if needed)
.venv\Scripts\python.exe windows_unity_game.py
```

Run elevated if your game window is admin-protected (anti-cheat, launcher
elevation) — otherwise mouse / keyboard events won't reach it.

## Why this pattern

- **Class-based HWND lookup** — matching by `UnityWndClass`, not title, rules
  out File Explorer windows that happen to show the game folder.
- **`ShellExecuteW`, not `subprocess`** — matches a real double-click, so UAC
  and shell hooks fire; many launchers refuse to start without them.
- **`record_window=True`** — crops capture to the game window's visible rect.
  Fullscreen capture often goes black on GPU-composited game windows.
- **Deterministic for the splash, AI for the audit** — the launch / splash
  flow is well-defined, so cheap `wait_for` + `click` is steadier than asking
  an LLM. The inventory walkthrough is open-ended, so `bot.ai()` earns its
  cost there.

See [windows_unity_game.py](windows_unity_game.py) for the full annotated
script and configurable environment variables.
