---
title: 自主 AI 任务与自定义工具
description: 用 bot.ai() 驱动多步任务——步骤回调、max_steps 预算、模型可在任务中途调用的自定义 Python 工具(API、数据库、取验证码、人工介入),以及裁剪内置工具。
---

# AI 任务与自定义工具

## bot.ai():自主循环

`bot.ai()` 把目标交给 AI。每一步它截取目标屏幕、推理下一步动作、视觉定位
元素并执行——循环直到目标达成或步数预算用尽:

```python
from qirabot import Qirabot, StepResult

bot = Qirabot()
page = bot.open("https://www.google.com")

def on_step(step: StepResult) -> None:
    status = "done" if step.finished else step.action_type
    print(f"  Step {step.step}: {status} {step.params}")

result = bot.ai(
    page,
    "搜索 'best python libraries 2026',点开第一条结果,提取正文内容",
    max_steps=10,
    on_step=on_step,
)
print(result.success, result.output)
bot.close()
```

运行如何结束记录在 `result.status`——四种结果和 `max_steps` 重试模式见
[错误处理](/zh/advanced/error-handling)。

## 自定义工具:让模型调用你的代码

`custom_tools` 把你自己的函数注册为模型在任务中途可调用的工具。任何
Python 函数都行——调内部 API、查数据库、从邮件服务器取验证码、造测试
数据、等人工介入。工具名、描述和参数 schema 从函数名、docstring 和签名
自动推导:

```python
def gm_command(command: str) -> str:
    """向游戏 GM 后端发送命令并返回其回复。
    可用命令:add_energy <amount>、add_gold <amount>、finish_quest <quest_id>
    """
    resp = requests.post(GM_URL, json={"cmd": command}, headers={"X-GM-Token": GM_TOKEN}, timeout=10)
    return resp.text

result = bot.ai(
    device,
    "完成所有日常任务。如果出现体力不足弹窗,"
    "用 gm_command 加 100 体力后继续",
    custom_tools=[gm_command],
    exclude_tools=["long_press"],   # 可选:裁掉任务用不到的内置工具
)
```

模型选中工具后,SDK 在**你的本地机器**执行它,并把返回值作为下一步的
观察反馈给模型。模型只看到工具的名字、描述和参数,以及工具的返回值——
永远看不到背后的代码、接口和凭据。

### 规则

- **docstring 必填** —— 它就是模型阅读的工具描述。参数类型来自注解
  (`str`/`int`/`float`/`bool`;其他类型退化为字符串);无默认值的参数标记
  为必填。lambda 和 `*args`/`**kwargs` 会被拒绝。每次调用最多 16 个工具。
- **字典形式(兜底)** —— 自动推导表达不了的 schema(枚举、逐参数描述):
  `{"name": ..., "description": ..., "parameters": {...}, "handler": fn}`。
- **返回值** —— 字符串化后作为动作结果展示给模型(`None` 变成 `"ok"`);
  抛出的异常以 `ERROR: ...` 回报给模型,让它能应对而不是整个运行挂掉。
- **`exclude_tools`** 按名字移除本次调用的内置工具(如 `"scroll"`、
  `"long_press"`)——防止模型误入任务不需要的动作。`done` 不可移除。
  工具名即[平台支持矩阵](/zh/reference/api#平台支持矩阵)中的动作名。
- 两个参数都是按 `ai()` 调用生效,绑定代理上同样可用。

### 人工介入(human-in-the-loop)

自定义工具可以直接阻塞等人操作——验证码和登录墙的标准解法:

```python
def wait_for_human(reason: str) -> str:
    """暂停任务,请人工介入(如解验证码)。人工完成后按回车返回。"""
    input(f"[需要人工] {reason} —— 完成后按回车: ")
    return "人工已完成,继续"
```

可运行示例:
[custom_tool_gm.py](https://github.com/qirabot/qirabot/blob/main/examples/game/custom_tool_gm.py)
·
[06_human_in_the_loop.py](https://github.com/qirabot/qirabot/blob/main/examples/automation/06_human_in_the_loop.py)

## 模型与语言

```python
bot = Qirabot(model="claude-vertex", language="zh")        # 全局默认
bot.verify(page, "每一行都显示折扣价",
           thinking_level="high")                          # 难的调用 → 多想想
```

`model` 为整个 bot 选择 Vertex AI 模型(`"{provider}/{model}"`);留空
使用默认的 `gemini-vertex/gemini-3.6-flash`。`thinking_level`
(`minimal`/`low`/`medium`/`high`)在同一模型内伸缩推理深度,可按调用
覆盖。详见[配置](/zh/advanced/configuration)。确定性的单步调用见
[API 参考](/zh/reference/api)。
