---
title: 常见问题 FAQ —— Qirabot
description: 需要什么 Google Cloud 凭据、哪些调用会请求模型、录屏黑屏怎么办、headless 自动降级、步骤间长时间等待等常见问题。
---

# 常见问题

## 需要什么凭据?

Google Cloud Application Default Credentials(ADC)——不需要 Qirabot
账号或 API key。决策引擎在 SDK 内本地运行,直接调用你自己项目里的
Google Vertex AI:把 `GOOGLE_APPLICATION_CREDENTIALS` 指向服务账号
JSON,或执行一次 `gcloud auth application-default login`(GCE 上自动
使用元数据服务器)。模型通过 `Qirabot(model="{provider}/{model}")` 或
`QIRA_MODEL` 选择——见[配置](/zh/advanced/configuration#模型与语言)。
`qirabot doctor` 可以校验整套配置。

## 哪些调用会请求模型,哪些不会?

走 AI 的调用会把截图发到你的 Vertex AI 端点(在那里消耗 token):
`ai()`、`extract`、`verify`、`wait_for`,以及 AI 定位的动作(带元素描述
的 `click`、`type_text`、`double_click`)。不经过 AI 的直接动作没有任何
开销:`navigate`、`go_back`、`close_tab`、`scroll`、`press_key`、
`screenshot`、`launch_app`、空 locate 的 `type_text`、不带 locate 的
`mouse_up`。[API 参考](/zh/reference/api)中标注了"无 AI"。模型用量由
Google Cloud 在你的项目上计费——每个结果对象都带 token 计数。

## 哪些数据会离开我的机器?

截图、指令文本和步骤元数据——直接从你的机器发往你配置的模型端点
(Google Vertex AI,在你自己的 Google Cloud 项目里),不经过任何
Qirabot 服务器。代码、cookie、凭据都留在本地;动作在你的机器上执行。
详见[数据与隐私](/zh/reference/privacy)。

## 录屏为什么是黑的?

- **Windows 且 `record_window=True`**:`gdigrab` 对最小化或 GPU 合成
  (独占全屏游戏)的窗口会录出黑帧——保持窗口可见,游戏建议录全屏。
- **macOS**:给终端/IDE 授予"屏幕录制"权限。

录屏是尽力而为:缺 ffmpeg 或权限被拒只警告、不会让任务失败——查看运行
目录里的 `recording.ffmpeg.log`。详见[报告与录屏](/zh/advanced/reports)。

## 浏览器为什么自动变成 headless 了?

在没有显示器的机器上(无 `DISPLAY`),`bot.open()` 和 CLI 会自动降级为
headless 并给出警告。显式传 `--headless` 可以让它无条件生效。

## 遇到 `MissingDependencyError` 怎么办?

某个可选后端依赖未安装。错误消息里给出了要执行的确切
`pip install "qirabot[<extra>]"` 命令;extras 清单见
[安装](/zh/guide/installation)。

## 脚本在步骤之间长时间等待,运行会超时吗?

不会。引擎在你的进程里本地运行——没有需要保活的服务端会话,`bot.*`
调用之间等多久都安全。详见
[配置](/zh/advanced/configuration#运行生命周期)。

## Android 上能输入中文和 emoji 吗?

能——`bot.type_text(...)` 开箱即用。超出 ASCII 的输入通过内置的
ADBKeyboard 输入法完成,按需安装、用完自动切回。见
[Android](/zh/backends/android)。

## 必须重写我现有的 Playwright / Selenium / Appium 套件吗?

不用。把你现有的 `page` 或 `driver` 作为目标传入,只在选择器难搞的地方
加 AI 步骤——见 [Playwright](/zh/frameworks/playwright)、
[Selenium](/zh/frameworks/selenium)、[Appium](/zh/frameworks/appium)、
[pytest](/zh/frameworks/pytest) 各集成指南。

## 我从 Airtest / qirabot 1.x 迁移过来

内置设备后端是即插即用的替代
(`connect_device(...)` → `AdbDevice` / `WdaClient` / `Window`),参考
adapter 能让老脚本原样运行。见
[从 Airtest 迁移](/zh/backends/custom-adapters#从-airtest-迁移-qirabot-1-x)。

## 我从 qirabot 2.x(云端引擎)迁移过来

v3 用本地决策引擎取代了云端引擎:没有账号、没有 `QIRA_API_KEY`、没有
`qirabot login`——改为配置 Google Cloud ADC 和模型。如果设置了
`QIRA_API_KEY` 却没有配置模型,`Qirabot()` 会在构造时抛出带迁移步骤的
明确错误。要保留旧的云端行为,固定版本安装:
`pip install "qirabot<3"`。
