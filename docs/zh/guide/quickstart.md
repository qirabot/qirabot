---
title: 快速开始
description: 两条命令跑通第一个 AI 驱动的 GUI 自动化任务,再用 Python SDK 实现同一任务——bot.ai() 自主任务与 AI 定位的确定性步骤。
---

# 快速开始

本页覆盖两条路:一条是 CLI,用一条 shell 命令跑自然语言任务,不用写代码;
另一条是 Python SDK。即使你是冲着 SDK 来的,也建议先用 CLI 跑一条,顺便
验证环境是否就绪。

决策引擎在 SDK 内本地运行,调用的是你自己 Google Cloud Vertex AI 端点上
的视觉模型,不涉及 Qirabot 账号,也没有 Qirabot 的 API key。先完成一次
Google Cloud
认证(Application Default Credentials),然后把任务交给 AI:

```bash
gcloud auth application-default login   # 一次即可;或将 GOOGLE_APPLICATION_CREDENTIALS 指向服务账号 JSON
qirabot browser "搜索 SpaceX 并提取词条的第一句话" --url wikipedia.org
```

如果不想安装 gcloud,`gemini-vertex` 系列模型也可以改用
[Vertex AI API key](https://cloud.google.com/vertex-ai/generative-ai/docs/start/api-keys)
认证:设置 `QIRA_VERTEX_API_KEY`。注意这是 Google Cloud 的 API
key,不是 AI Studio 的 key;仅支持 Google 自家模型,只走全局端点。
如果完全不想碰 Google Cloud,可以用 `gemini` provider
(`QIRA_MODEL=gemini/gemini-3.6-flash`),它直接调用
[Gemini Developer API](https://ai.google.dev/gemini-api/docs/api-key),
用 AI Studio 的 key:设置 `QIRA_GEMINI_API_KEY`(或 `GEMINI_API_KEY`)。

这就是一次完整运行:浏览器打开,AI 完成任务,结果(和一份 HTML 报告)输出
到终端。所有命令和选项见 [CLI 参考](/zh/guide/cli)。

不做任何其他配置时,运行使用 `gemini-vertex/gemini-3.6-flash`,项目
取自 ADC 凭据自身。要显式指定模型或项目,设置 `QIRA_MODEL` /
`QIRA_VERTEX_PROJECT`(或向 `Qirabot()` 传 `model=` / `vertex_project=`,
CLI 上用 `-m` / `--vertex-project`):

```bash
export QIRA_MODEL="gemini-vertex/gemini-3.6-pro"   # "{provider}/{model}"
export QIRA_VERTEX_PROJECT="my-gcp-project"
```

browser 命令假定你走的是一行安装脚本或 `pip install "qirabot[browser]"`
路径。如果为设备后端只装了核心 `qirabot`,各 extra 见
[安装](/zh/guide/installation)。

## 用 Python 实现同一任务

`bot.ai()` 就是 CLI 命令底层的引擎:AI 看屏、决定下一步动作,循环执行直到
任务完成:

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

如果想自己掌控每一步,而不是把整个任务交给 AI,同样的自然语言定位能力
也可以按单步调用。这种方式更快、成本更低,控制流始终在你的代码里:

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://www.saucedemo.com")

# 用自然语言描述元素(任何语言都行);AI 视觉定位,代码由你掌控:
bot.type_text(page, "用户名输入框", "standard_user")
bot.type_text(page, "密码输入框", "secret_sauce")
bot.click(page, "登录按钮")

# 基于视觉状态设卡点——wait_for 轮询直到成立,超时抛异常
bot.wait_for(page, "商品列表页已显示")

# 直接从屏幕提取结构化数据——不写爬取逻辑、不写选择器
count = bot.extract(page, "购物车角标上的数字,返回整数")

bot.close()
```

核心调用:

| 调用 | 作用 |
|---|---|
| `bot.ai(target, task)` | 自主多步任务:看屏、决策、执行,循环直到完成 |
| `bot.click(target, "描述")` | AI 定位的点击(另有 `double_click`、`type_text`) |
| `bot.extract(target, "描述")` | 从屏幕提取结构化数据 |
| `bot.verify(target, "断言")` | 视觉断言,结果为 truthy/falsy;断言不成立不抛异常 |
| `bot.wait_for(target, "条件")` | 轮询直到视觉条件成立,超时抛异常 |

`target` 就是你正在驱动的界面:`bot.open()` 返回的 page、你自己的
Playwright/Selenium/Appium 对象,或桌面场景下的 `pyautogui` 模块。完整
调用列表和各平台行为见 [API 参考](/zh/reference/api)。

## 任务如何结束

`result.success` 是二值的通过/失败;`result.status` 说明原因:
`"completed"`、`"goal_failed"`(登录墙、验证码)、`"max_steps"`(步数预算
截断,加大预算重试)、`"error"`。详情和异常体系见
[错误处理](/zh/advanced/error-handling)。

```python
result = bot.ai(page, "找到最便宜的航班并锁定")
if result.status == "max_steps":
    # 不是真的失败——预算太小;加大步数重试
    result = bot.ai(page, "找到最便宜的航班并锁定", max_steps=50)
```

## 报告

每次运行都会在 `./qira_runs/<日期>/<时间-id>/` 写入一份自包含的 HTML 报告,
带逐步截图。出错或 Ctrl+C 中断时也会生成,方便定位在哪一步停下。传
`record=True`(CLI 用 `--record`)还能录制整个运行过程的视频。

## 下一步

- 选择你的后端:[浏览器](/zh/backends/browser) ·
  [Android](/zh/backends/android) · [iOS](/zh/backends/ios) ·
  [Windows 与游戏](/zh/backends/windows-games) · [桌面](/zh/backends/desktop)
- 如果要挂载到现有 Playwright / Selenium / Appium 套件,见
  [自定义 Adapter 与挂载](/zh/backends/custom-adapters)
- [CLI 参考](/zh/guide/cli)
