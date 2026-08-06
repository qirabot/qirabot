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
    AuthenticationError,       # 凭据 / 迁移配置问题
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
login`。`AuthenticationError` 还有一种在构造时抛出的情况:设置了残留的
v2 `QIRA_API_KEY` 却没有配置模型,此时消息会解释 v3 迁移方法(配置 ADC 和
模型,或固定 `qirabot<3` 保留旧的云端行为)。

所有异常都派生自 `QirabotError`,所以单独一个
`except QirabotError` 永远是安全的兜底:

| 异常 | 时机 |
|---|---|
| `AuthenticationError` | 凭据配置问题,包括 v3 迁移守卫(设置了 `QIRA_API_KEY` 却没有配置模型)。不重试。 |
| `RateLimitError` | 模型提供方限流(429)。引擎内部会退避并重试;捕获它可加自己的退避策略。 |
| `QirabotTimeoutError` | 客户端等待超时(`wait_for`、自动等待)。 |
| `ActionError` | AI 动作失败,包括你的 Vertex AI 端点报告的模型调用失败(消息携带提供方的详细信息)。 |
| `MissingDependencyError` | 某个可选后端依赖(playwright、pyautogui 等)未安装;消息里给出要执行的确切 `pip install "qirabot[<extra>]"`。同时也是 `ImportError`。 |

(`InsufficientBalanceError`、`QirabotConnectionError`、
`TaskTerminatedError` 仍可导入,以兼容 v2 的捕获代码,但 v3 本地引擎
不再抛出它们:已不存在 Qirabot 服务器、计费或服务端任务状态。)

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
