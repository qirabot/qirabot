"""knowledge=: give bot.ai() the game rules so it doesn't get stuck.

When the model doesn't know your game (what an item icon means, how a dungeon
flow works), it stalls or guesses. knowledge= supplies that background as
reference material in the system prompt — separate from the instruction, so a
rules document is never mistaken for the task itself. Pass the text, a local
file as pathlib.Path (UTF-8), or a list mixing both; for a remote source fetch
the text yourself (requests.get(url).text) and pass it.

Scope is per bot.ai() call, which makes staged loading natural: each stage
mounts only its own knowledge, and whatever the previous stage used is gone.

Hard rules are a different thing: "GM may only be used once" enforced in the
tool handler is a guarantee; written in knowledge it is only a suggestion.
Do both — the knowledge line saves the model a wasted attempt, the handler
guard makes violation impossible.

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/game/knowledge.py
"""

from pathlib import Path

import requests

from qirabot import AdbDevice, Qirabot

GM_URL = "http://internal-gm.example.com/exec"
GM_TOKEN = "replace-me"

gm_used = False


def gm_command(command: str) -> str:
    """Send a command to the game's GM backend and return its reply.
    Available commands: add_energy <amount>, add_gold <amount>, finish_quest <quest_id>.
    The whole task may use GM only once.
    """
    global gm_used
    if gm_used:  # enforced here, not just in the prompt: code cannot be talked around
        return "GM already used once this task; solve it through normal play instead."
    gm_used = True
    resp = requests.post(GM_URL, json={"cmd": command}, headers={"X-GM-Token": GM_TOKEN}, timeout=10)
    return resp.text


bot = Qirabot(task_name="daily-dungeon-knowledge")
device = AdbDevice()

# Stage 1: tutorial. GM tool and tutorial knowledge mounted for this call only.
result = bot.ai(
    device,
    "Finish the beginner tutorial",
    max_steps=30,
    custom_tools=[gm_command],
    knowledge=Path(__file__).with_name("game_rules.md"),  # local file, read as UTF-8
)
print(f"Tutorial: {result.success}")

# Stage 2: daily dungeon. No GM tool this time (stronger than telling the model
# not to use it), and stage-specific knowledge replaces the tutorial's.
result = bot.ai(
    device,
    "Complete today's daily dungeon runs",
    max_steps=30,
    knowledge=[
        "每日副本上限 3 次，入口在主界面右下角“冒险”按钮。",
        "体力不足时不要反复点击入口，先领取邮箱里的每日体力。",
    ],
)
print(f"Dungeon: {result.success}")
print(f"Output: {result.output}")
bot.close()
