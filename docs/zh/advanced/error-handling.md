---
title: 错误处理与运行结果
description: Qirabot 的异常体系、ai() 运行的四种 result.status 结果、max_steps 重试模式、动作自动重试,以及失败在 HTML 报告中的呈现。
---

# 错误处理

## 异常

```python
from qirabot import (
    Qirabot,
    QirabotError,              # 基类
    AuthenticationError,       # 凭据配置问题
    QirabotTimeoutError,       # wait_for / 自动等待超时
)

try:
    # 构造函数本身就可能抛异常:它会校验模型配置并解析 Google Cloud
    # 凭据(ADC),配置有误在这里就失败,而不是运行到一半。
    # `with` 保证 close() 在——且仅在——构造成功时执行。
    with Qirabot() as bot:
        page = bot.open("https://example.com")
        bot.click(page, "登录按钮")
except AuthenticationError:
    print("凭据配置问题——错误消息里有修复方法。")
except QirabotTimeoutError:
    print("操作超时。")
except QirabotError as e:
    print(f"错误: {e}")
```

配置类错误在构造时暴露:`model="{provider}/{model}"` 里 provider 未知
或缺少模型,以及缺少 Google Cloud 项目,会抛出带配置提示的
`ValueError`;Google Cloud 凭据缺失或不可用时,错误消息会指向
`GOOGLE_APPLICATION_CREDENTIALS` / `gcloud auth application-default
login`。

所有异常都派生自 `QirabotError`,所以单独一个
`except QirabotError` 永远是安全的兜底:

| 异常 | 时机 |
|---|---|
| `AuthenticationError` | 凭据配置问题——凭据缺失、不可用或有歧义。环境里残留 v2 时代的 `QIRA_API_KEY` 时也会抛出。不重试。 |
| `QirabotTimeoutError` | 客户端等待超时(`wait_for`、自动等待)。 |
| `ActionError` | AI 动作失败,包括你的 Vertex AI 端点报告的模型调用失败(消息携带提供方的详细信息)。 |
| `MissingDependencyError` | 某个可选后端依赖(playwright、pyautogui 等)未安装;消息里会按 qirabot 当前所处的环境给出要执行的确切安装命令。同时也是 `ImportError`。 |

这张表就是异常体系的全部。云端时代的那几个异常
(`RateLimitError`、`InsufficientBalanceError`、`QirabotConnectionError`、
`TaskTerminatedError`)已在 **v3.2 移除**——已不存在 Qirabot 服务器、
计费和服务端任务状态,它们没有对应的东西可描述了。现在导入会直接失败,
把针对它们的 `except` 分支删掉,或者放宽成 `QirabotError`。

**限流(429)不会以独立异常的形式到达你的代码。** provider 层用专门的
退避策略在内部重试:5s、10s、20s、30s,累计跨满一个配额分钟窗——被拒的
429 不计费,多等的成本为零。只有熬过全部重试仍然限流的情况才会暴露出来,
形式是 `ActionError`。

`verify()` 是"失败即抛异常"语义的刻意例外:断言不成立不抛异常,而是
返回 falsy 结果(`VerifyResult`,其 `.reason` 说明原因),可直接用于
`assert` 或 `if`。模型调用和凭据错误仍像其他调用一样抛出。

瞬时的动作失败会自动重试(默认 `retry=1`、`retry_delay=1.0`,见
[配置](/zh/advanced/configuration))。

## ai() 运行如何结束:result.status

`result.success` 是二值判定,但失败的运行可能意味着很不一样的事情:

| status | 含义 | `success` |
|---|---|---|
| `"completed"` | 模型判定目标已达成 | `True` |
| `"goal_failed"` | 模型判定目标不可达(登录墙、验证码) | `False` |
| `"max_steps"` | 步数预算用尽;是截断,不是能力判定 | `False` |
| `"error"` | 引擎遇到终止性错误(如模型调用失败) | `False` |

`max_steps` 值得专门处理,它是预算问题,不是能力问题:

```python
result = bot.ai(page, "找到最便宜的航班并锁定")
if result.status == "max_steps":
    # 不是真的失败——预算太小;加大步数重试
    result = bot.ai(page, "找到最便宜的航班并锁定", max_steps=50)
```

`goal_failed` 通常意味着环境需要帮助,比如登录墙或验证码。可以考虑
[人工介入的自定义工具](/zh/advanced/ai-tasks#人工介入-human-in-the-loop),
让模型求助而不是放弃。

## 失败在报告中的呈现

以抛异常结束的运行不会产生 `RunResult`;在 [HTML 报告](/zh/advanced/reports)
里对应区块的徽章是 `ERROR`。报告在异常和 Ctrl+C 之后也会写出,包含
直到失败为止的逐步截图,通常这是看清屏幕上到底发生了什么的最快方式。

报告头部的汇总:全部通过为绿色,只有 `MAX STEPS` 截断为琥珀色,存在真正
失败为红色。

要自行记录一次运行的终态(让报告显示失败或取消而不是成功),在关闭前
调用 `bot.fail()` / `bot.cancel()`;两者都是本地的运行记账,见
[API 参考](/zh/reference/api#任务生命周期)。

## 自定义工具的错误

自定义工具抛异常不会杀死运行:异常以 `ERROR: ...` 回报给模型,模型可以
应对:重试、换路径,或以 `goal_failed` 结束。见
[AI 任务与自定义工具](/zh/advanced/ai-tasks)。
