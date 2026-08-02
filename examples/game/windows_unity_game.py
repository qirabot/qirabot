"""Drive a Windows desktop game with Qirabot — splash launch + AI UI audit.

Pattern:
  1. Launch the game executable via ShellExecuteW (matches a real double-click —
     follows UAC and shell hooks that subprocess.Popen would skip, which most
     launchers require).
  2. Bind with ``Window(class_name=..., timeout=...)`` — matching the renderer
     window by *class name* (Unity games expose ``UnityWndClass``; Unreal uses
     ``UnrealWindow``) rules out File Explorer windows whose title happens to
     share the game name, and ``timeout`` keeps polling while the game boots.
     Input goes out as DirectInput scancodes that game engines can read.
  3. Drive splash → in-game with deterministic ``wait_for`` / ``click`` /
     ``press_key`` steps (cheaper and steadier than AI for known UI), then
     hand the open-ended UI audit to ``bot.ai``.

Recording is built-in: ``record_window=True`` crops to the game window's
visible rect (GPU-composited windows safe — fullscreen capture would otherwise
go black on most games), and ``record_audio=True`` auto-detects a loopback
device.

Auth is Google Cloud ADC (``gcloud auth application-default login`` once).
Set ``QIRA_GAME_EXE`` (and optionally ``QIRA_MODEL`` / ``QIRA_VERTEX_PROJECT``)
in a ``.env`` next to this script, adjust the constants below for your game,
and run elevated so input reaches admin-protected game windows:

    .venv\\Scripts\\python.exe windows_unity_game.py
"""

import ctypes
import os
import time

from qirabot import Qirabot, QirabotError, StepResult, Window, load_dotenv

# ---- config ---------------------------------------------------------------

WND_CLASS = "UnityWndClass"  # UnrealWindow for Unreal games
LAUNCH_BUTTON = "The Play / Enter Game button on the splash screen"
IN_GAME = "In-game HUD visible (e.g. minimap, character, status bars)"
AI_TASK = (
    "Open the character and inventory menus, verify each tab loads "
    "correctly, then return to the main view."
)


def launch(game_exe: str) -> None:
    print(f"launching: {game_exe}")
    rc = ctypes.windll.shell32.ShellExecuteW(
        None, "open", game_exe, None, os.path.dirname(game_exe), 1
    )
    if rc <= 32:
        raise RuntimeError(f"ShellExecuteW failed (code {rc})")


def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params} — {step.decision}")


def main() -> None:
    game_exe = os.environ.get("QIRA_GAME_EXE")
    if not game_exe:
        raise RuntimeError("set QIRA_GAME_EXE to your game's executable path")

    window = Window(class_name=WND_CLASS)
    try:
        hwnd = window.hwnd  # already running?
    except QirabotError:
        launch(game_exe)
        window = Window(class_name=WND_CLASS, timeout=180)
        hwnd = window.hwnd
    print(f"binding to hwnd={hwnd}")

    with Qirabot(
        task_name="windows-game-ui-check",
        record=True,
        record_window=True,
        record_audio=True,
        record_fps=30,
    ).bind(window) as bot:
        # Splash UI is often not responsive for the first few seconds after
        # the launcher hands off to the game process.
        print("waiting for splash to settle...")
        time.sleep(15)

        # Deterministic: locate the splash launch button, click it.
        print(f"waiting for launch button: {LAUNCH_BUTTON!r}")
        bot.wait_for(LAUNCH_BUTTON, timeout=180, interval=5)
        bot.click(LAUNCH_BUTTON)

        # Wait for actual in-game state before doing anything else.
        print(f"waiting for in-game state: {IN_GAME!r}")
        time.sleep(10)
        bot.wait_for(IN_GAME, timeout=180, interval=5)

        # Most games open their main menu on Escape.
        print("pressing ESC to open the menu")
        bot.press_key("esc")

        # Open-ended audit — let the AI drive.
        result = bot.ai(AI_TASK, max_steps=50, on_step=on_step)
        print("success:", result.success)
        print("output:", result.output)
        print("report:", bot.report_dir)


if __name__ == "__main__":
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    main()
