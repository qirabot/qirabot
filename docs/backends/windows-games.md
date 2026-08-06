---
title: Automate Windows Apps & Games with AI — DirectInput Scancodes
description: Bind one Windows window by title or HWND and drive it with AI vision. Input is DirectInput scancodes that Unity, Unreal, and native games actually read — where virtual-key automation fails.
---

# Windows & Games — the Window backend

`qirabot.Window` binds to a single window (by title regex or HWND).
Screenshots are its client area, clicks are window-relative, and keys are
**DirectInput scancodes**: the level games poll, which virtual-key
automation (pyautogui, AutoHotkey's default send mode) can't reach. It uses
stdlib ctypes only and is built into the core package, so there are no
extras to install.

Combined with AI vision for element location, this reaches targets that
DOM- and accessibility-based frameworks cannot: Unity and Unreal games,
custom launchers, legacy native apps.

The quickest check is the CLI, which is built in and needs no extras:

```bash
qirabot desktop "Open the inventory and list all items" --window-title "Genshin"
qirabot desktop "..." --hwnd 132456
```

The same thing in Python:

```python
from qirabot import Qirabot, Window

window = Window(title="Genshin")   # literal substring; or Window(hwnd=132456)
bot = Qirabot().bind(window)

result = bot.ai("Open the inventory and list all items")
bot.close()
```

`Window` selectors: `hwnd=` (explicit handle), `title=` (literal substring;
paste the title straight from the taskbar, parentheses and dots are safe),
`title_re=` (a regex, for fuzzy/multi-language matching), or `class_name=`
(exact window class: Unity games expose `UnityWndClass`, Unreal
`UnrealWindow`; steadier than titles and combinable with `title`/`title_re`).
If several windows match, resolution fails listing the candidates. When the
duplicates are unavoidable (cloud-gaming clients and launcher overlays often
share the main window's exact title), add `ambiguous="largest"` (CLI:
`--ambiguous largest`) to pick the biggest window. The console window running
qirabot is never a candidate: its title echoes the command line, pattern
included, and would otherwise match itself. `timeout=` keeps polling for the
window while a game is still starting:

```python
window = Window(title="MyGame · Cloud(Beta)", ambiguous="largest")
window = Window(class_name="UnityWndClass", timeout=180)   # just-launched game
```

Before each typing/keypress call, the backend switches the focused control's
input language to US English and closes its IME, since an active CJK IME
would swallow injected letter keys into its composition window instead of
the game. The switch is re-asserted every time (IME state belongs to the focused
control and comes back whenever a text box takes focus), and it is verified:
a window that refuses to give up its IME gets text via clipboard paste, which
bypasses IME composition entirely. Typing CJK text never needs a CJK IME:
non-ASCII strings always travel the paste path, so forcing English input
loses nothing. Only the target window is touched (Win+Space switches it
back); pass `Window(..., english_ime=False)` to leave the IME alone.

## Game-grade input

- Keys are scancodes: real hardware-level input, including
  `ctrl`/`alt`/`win` combos. Characters outside the scancode table are
  injected as unicode key events.
- Hold a key for a duration to get quantified in-game movement:

  ```python
  bot.press_key(window, "w", duration_seconds=2)      # walk forward 2s
  bot.press_key(window, "shift+w", duration_seconds=1.5)  # sprint
  ```

- Modifier-click: an atomic alt+click for games, or ctrl+click multi-select:

  ```python
  bot.click(window, "enemy unit", modifier="alt")
  ```

- Split press/release primitives: `mouse_down` / `mouse_up` /
  `key_down` / `key_up` hold an input across other actions (keep moving while
  clicking, press-and-hold drags). Any input still held is auto-released at
  the end of an `ai()` run and on `close()`.

## Mixing deterministic steps with AI

Game UI audits work well as deterministic navigation plus AI verification:

```python
bot.click(window, "the Bag icon")
bot.wait_for(window, "the inventory panel is open")
ok = bot.verify(window, "every item slot shows an icon and a count")
items = bot.extract(window, "list the item names visible in the inventory")
```

See the full walkthrough in
[examples/game/](https://github.com/qirabot/qirabot/tree/main/examples/game),
including a custom-tool example where the AI calls your GM backend mid-task
(add energy on an out-of-energy popup, then continue the daily-quest loop).
How to register such tools is covered in
[AI Tasks & Custom Tools](/advanced/ai-tasks).

## Recording the window

On Windows you can record just the window under test, with system audio:

```python
bot = Qirabot(record=True, record_window=True, record_audio=True)
```

Keep the window visible: `gdigrab` produces black frames for minimized or
GPU-composited (fullscreen-exclusive game) windows. For those, record the
full screen instead.

## Notes

- Whole-desktop automation (any OS) is the separate
  [pyautogui backend](/backends/desktop); the Window backend is
  Windows-specific and single-window by design.
- If you are coming from Airtest 1.x, `connect_device("Windows:///132456")`
  becomes `Window(hwnd=132456)`.
