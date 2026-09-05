---
title: Autonomous AI Tasks & Custom Tools
description: Drive multi-step tasks with bot.ai() - step callbacks, max_steps budgets, custom Python tools the model can call mid-task (APIs, databases, OTP fetch, human-in-the-loop), pruning built-in tools, and mounting knowledge or existing skill files (SKILL.md).
---

# AI Tasks & Custom Tools

## bot.ai(): the autonomous loop

`bot.ai()` hands the AI a goal. On each step it screenshots the target,
reasons about the next action, locates the element visually, and executes
it. The loop runs until the goal is met or the step budget runs out:

```python
from qirabot import Qirabot, StepResult

bot = Qirabot()
page = bot.open("https://www.google.com")

def on_step(step: StepResult) -> None:
    status = "done" if step.finished else step.action_type
    print(f"  Step {step.step}: {status} {step.params}")

result = bot.ai(
    page,
    "Search for 'best python libraries 2026', click the first result, and extract the main content",
    max_steps=10,
    on_step=on_step,
)
print(result.success, result.output)
bot.close()
```

How the run ended is recorded in `result.status`. See
[Error Handling](/advanced/error-handling) for the four outcomes and the
`max_steps` retry pattern.

## Custom tools: let the model call your code

`custom_tools` registers your own functions as tools the model can invoke
mid-task. Any Python function works: hit an internal API, query a database,
fetch an OTP from your mail server, seed test data, or pause for a human. The
tool name, description, and parameter schema are introspected from the
function name, docstring, and signature:

```python
def gm_command(command: str) -> str:
    """Send a command to the game's GM backend and return its reply.
    Available commands: add_energy <amount>, add_gold <amount>, finish_quest <quest_id>
    """
    resp = requests.post(GM_URL, json={"cmd": command}, headers={"X-GM-Token": GM_TOKEN}, timeout=10)
    return resp.text

result = bot.ai(
    device,
    "Complete every daily quest. If an out-of-energy popup appears, "
    "use gm_command to add 100 energy and continue",
    custom_tools=[gm_command],
    exclude_tools=["long_press"],   # optional: prune built-ins the task never needs
)
```

When the model picks a tool, the SDK runs it locally on your machine and
feeds the return value back as the observation for the next step. The
model sees only the tool's name, description, and parameters, plus
whatever the tool returns. It never sees the code, endpoint, or
credentials behind it.

### Rules

- A docstring is required; it becomes the tool description the model reads.
  Parameter types come from annotations (`str`/`int`/`float`/`bool`; anything
  else falls back to string); parameters without defaults are required.
  Lambdas and `*args`/`**kwargs` are rejected. At most 16 tools per call.
- For schemas introspection can't express (enums, per-parameter
  descriptions), there is a dict escape hatch:
  `{"name": ..., "description": ..., "parameters": {...}, "handler": fn}`.
- The return value is stringified and shown to the model as the action
  result (`None` becomes `"ok"`). A raised exception is reported back as
  `ERROR: ...` so the model can react instead of the run dying.
- `exclude_tools` removes built-in tools by name (e.g. `"scroll"`,
  `"long_press"`) for this call, which keeps the model from wandering into
  actions the task never needs. `done` cannot be excluded. Tool names are
  the action names in the
  [platform support matrix](/reference/api#platform-support-matrix).
- Both parameters are per-`ai()`-call and also work on a bound bot.

### Human-in-the-loop

A custom tool can simply block until a human acts. This is the standard
pattern for captchas and login walls:

```python
def wait_for_human(reason: str) -> str:
    """Pause the task and ask a human to intervene (e.g. solve a captcha).
    Returns after the human presses Enter."""
    input(f"[HUMAN NEEDED] {reason} — press Enter when done: ")
    return "human finished, continue"
```

Runnable examples:
[custom_tool_gm.py](https://github.com/qirabot/qirabot/blob/main/examples/game/custom_tool_gm.py)
·
[06_human_in_the_loop.py](https://github.com/qirabot/qirabot/blob/main/examples/automation/06_human_in_the_loop.py)

## Knowledge & skill files

`knowledge` mounts reference material (game rules, business terms, an
operating procedure) for one `ai()` call, as text, a `Path` to a UTF-8
file, or a list mixing both (32KB total). It sits in the system prompt
apart from the instruction, so a rules document is never mistaken for the
task itself, and the next `ai()` call starts clean:

```python
result = bot.ai(
    device,
    "Complete every daily quest",
    knowledge=[Path("game-rules.md"), "GM commands may be used once per match"],
)
```

Knowledge guides decisions; it cannot enforce them. A hard rule ("once
per match") belongs in tool handler code too, where violating it is
impossible rather than discouraged.

### Reusing an existing skill

Instructions you already maintain for another agent, such as an agent
skill (`SKILL.md`), a runbook, or an internal SOP, mount unchanged via
`knowledge=`. One gap needs covering: built-in tools are GUI actions
only, so where the file says "run this command", the model has nothing to
run it with. Grant that ability explicitly, as a custom tool whose
allowlist lives in code:

```python
import shlex
import subprocess
from pathlib import Path

ALLOWED = {"npm", "git"}  # exactly the commands the skill file needs

def run_command(command: str) -> str:
    """Run a CLI command and return its output. Allowed commands: npm, git."""
    argv = shlex.split(command)
    if not argv or argv[0] not in ALLOWED:
        return f"command not allowed: {argv[0] if argv else '(empty)'}"
    r = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    out = (r.stdout + r.stderr).strip()
    return out or f"(no output, exit code {r.returncode})"

result = bot.ai(
    page,
    "Follow the release checklist to publish version 2.4.1",
    knowledge=Path("SKILL.md"),
    custom_tools=[run_command],
)
```

There is deliberately no built-in shell tool: which commands the model
may run is your decision, and putting that decision in handler code makes
it enforced rather than merely requested in a prompt. Keep the
`shlex.split` + argv form above (`shell=False`) so extra commands can't
ride in through `;` or `$(...)`. The return value becomes the model's
next observation, so trim chatty command output in the handler instead
of spending the model's context on it.

Runnable example:
[knowledge.py](https://github.com/qirabot/qirabot/blob/main/examples/game/knowledge.py)

## Model & language

```python
bot = Qirabot(model="gemini/gemini-3.8-flash", language="zh")        # defaults for all calls
bot.verify(page, "every row shows the discounted price",
           thinking_level="high")                          # think harder on hard calls
```

`model` selects the provider and model (`"{provider}/{model}"`) for the whole
bot; leave it unset for the default
`gemini-vertex/gemini-3.8-flash`. `thinking_level`
(`minimal`/`low`/`medium`/`high`) scales reasoning depth within the same
model and can be overridden per call. See
[Configuration](/advanced/configuration). Deterministic single-step calls
are covered in the [API reference](/reference/api).
