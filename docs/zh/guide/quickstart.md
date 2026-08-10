---
title: 快速开始
description: 一条命令跑通第一个 AI 驱动的 GUI 自动化任务，再用 Python SDK 实现同一任务——bot.ai() 自主任务与 AI 定位的确定性步骤。
---

# 快速开始

决策引擎在 SDK 内本地运行，调用的是你自己 Google Cloud Vertex AI 端点上
的视觉模型。先完成一次 Google Cloud 认证，然后把任务交给 AI：

```bash
gcloud auth application-default login   # 一次即可
qirabot browser "搜索 SpaceX 并提取词条的第一句话" --url wikipedia.org
```

浏览器打开，AI 完成任务，结果（和一份 HTML 报告）输出到终端。其余命令和
选项见 [CLI 参考](/zh/guide/cli)。

默认使用 `gemini-vertex/gemini-3.6-flash`，项目取自凭据自身。要更换模型或
项目，或改用 API key 而不是 gcloud 认证，见
[配置](/zh/advanced/configuration)。

## 用 Python 实现同一任务

`bot.ai()` 就是 CLI 命令底层的引擎：AI 看屏、决定下一步动作，循环执行直到
任务完成：

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://www.wikipedia.org")

result = bot.ai(page, "搜索 SpaceX 并提取词条的第一句话")
print(f"Success: {result.success}")
print(f"Result: {result.output}")

bot.close()
```

## 确定性步骤

如果想自己掌控每一步，而不是把整个任务交给 AI，同样的自然语言定位能力
也可以按单步调用。这种方式更快、成本更低，控制流始终在你的代码里：

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://www.saucedemo.com")

# 用自然语言描述元素（任何语言都行）；AI 视觉定位，代码由你掌控：
bot.type_text(page, "用户名输入框", "standard_user")
bot.type_text(page, "密码输入框", "secret_sauce")
bot.click(page, "登录按钮")

# 基于视觉状态设卡点——wait_for 轮询直到成立，超时抛异常
bot.wait_for(page, "商品列表页已显示")

# 直接从屏幕提取结构化数据——不写爬取逻辑、不写选择器
count = bot.extract(page, "购物车角标上的数字，返回整数")

bot.close()
```

核心调用：

| 调用 | 作用 |
|---|---|
| `bot.ai(target, task)` | 自主多步任务：看屏、决策、执行，循环直到完成 |
| `bot.click(target, "描述")` | AI 定位的点击（另有 `double_click`、`type_text`） |
| `bot.extract(target, "描述")` | 从屏幕提取结构化数据 |
| `bot.verify(target, "断言")` | 视觉断言，结果为 truthy/falsy；断言不成立不抛异常 |
| `bot.wait_for(target, "条件")` | 轮询直到视觉条件成立，超时抛异常 |

`target` 就是你正在驱动的界面：`bot.open()` 返回的 page、你自己的
Playwright/Selenium/Appium 对象，或桌面场景下的 `pyautogui` 模块。完整
调用列表和各平台行为见 [API 参考](/zh/reference/api)。

## 任务如何结束

`result.success` 是二值的通过/失败；`result.status` 说明原因：
`"completed"`、`"goal_failed"`（登录墙、验证码）、`"max_steps"`（步数预算
用尽）、`"error"`。详情和异常体系见
[错误处理](/zh/advanced/error-handling)。

```python
result = bot.ai(page, "找到最便宜的航班并锁定")
if result.status == "max_steps":
    # 不是真的失败——预算太小，加大步数重试
    result = bot.ai(page, "找到最便宜的航班并锁定", max_steps=50)
```

## 报告

每次运行都会在 `./qira_runs/<日期>/<时间-id>/` 写入一份自包含的 HTML 报告，
带逐步截图；出错或 Ctrl+C 中断时也会生成，方便定位在哪一步停下。传
`record=True`（CLI 用 `--record`）还能录制视频。

## 下一步

- 选择你的后端：[浏览器](/zh/backends/browser) ·
  [Android](/zh/backends/android) · [iOS](/zh/backends/ios) ·
  [Windows 与游戏](/zh/backends/windows-games) · [桌面](/zh/backends/desktop)
- 如果要挂载到现有 Playwright / Selenium / Appium 套件，见
  [自定义 Adapter 与挂载](/zh/backends/custom-adapters)
- [CLI 参考](/zh/guide/cli)
