---
title: Autonomous AI Tasks & Custom Tools
description: Drive multi-step tasks with bot.ai() - step callbacks, max_steps budgets, custom Python tools the model can call mid-task (APIs, databases, OTP fetch, human-in-the-loop), and pruning built-in tools.
---

# AI Tasks & Custom Tools

## bot.ai(): the autonomous loop

`bot.ai()` hands the AI a goal. Each step it screenshots the target, reasons
about the next action, locates the element visually, and executes — looping
until the goal is met or the step budget runs out:

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

How the run ended is in `result.status` — see
[Error Handling](/advanced/error-handling) for the four outcomes and the
`max_steps` retry pattern.

## Custom tools: let the model call your code

`custom_tools` registers your own functions as tools the model can invoke
mid-task. Any Python function works — hit an internal API, query a database,
fetch an OTP from your mail server, seed test data, pause for a human. The
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

When the model picks a tool, the SDK runs it **locally on your machine**
and feeds the return value back as the observation for the next step. The
model only sees the tool's name, description, and parameters, plus whatever
the tool returns — never the code, endpoint, or credentials behind it.

### Rules

- **Docstring required** — it becomes the tool description the model reads.
  Parameter types come from annotations (`str`/`int`/`float`/`bool`; anything
  else falls back to string); parameters without defaults are required.
  Lambdas and `*args`/`**kwargs` are rejected. At most 16 tools per call.
- **Dict form (escape hatch)** — for schemas introspection can't express
  (enums, per-parameter descriptions):
  `{"name": ..., "description": ..., "parameters": {...}, "handler": fn}`.
- **Return value** — stringified and shown to the model as the action result
  (`None` becomes `"ok"`); a raised exception is reported back as
  `ERROR: ...` so the model can react instead of the run dying.
- **`exclude_tools`** removes built-in tools by name (e.g. `"scroll"`,
  `"long_press"`) for this call — keeps the model from wandering into
  actions the task never needs. `done` cannot be excluded. Tool names are
  the action names in the
  [platform support matrix](/reference/api#platform-support-matrix).
- Both parameters are per-`ai()`-call and also work on a bound bot.

### Human-in-the-loop

A custom tool can simply block until a human acts — the standard pattern for
captchas and login walls:

```python
def wait_for_human(reason: str) -> str:
    """Pause the task and ask a human to intervene (e.g. solve a captcha).
    Returns after the human presses Enter."""
    input(f"[HUMAN NEEDED] {reason} — press Enter when done: ")
    return "human finished, continue"
```

Runnable examples:
[custom_tool_gm.py](https://github.com/qirabot/qirabot-python/blob/main/examples/game/custom_tool_gm.py)
·
[06_human_in_the_loop.py](https://github.com/qirabot/qirabot-python/blob/main/examples/automation/06_human_in_the_loop.py)

## Model & language

```python
bot = Qirabot(model="claude-vertex", language="zh")        # defaults for all calls
bot.verify(page, "every row shows the discounted price",
           thinking_level="high")                          # think harder on hard calls
```

`model` selects the Vertex AI model (`"{provider}/{model}"`) for the whole
bot; leave it unset for the default
`gemini-vertex/gemini-3-flash-preview`. `thinking_level`
(`minimal`/`low`/`medium`/`high`) scales reasoning depth within the same
model and can be overridden per call. See
[Configuration](/advanced/configuration). Deterministic single-step calls
are covered in the [API reference](/reference/api).
