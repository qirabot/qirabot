---
title: 常见问题 FAQ —— Qirabot
description: 需要什么 Google Cloud 凭据、哪些调用会请求模型、录屏黑屏怎么办、headless 自动降级、步骤间长时间等待等常见问题。
---

# 常见问题

## 需要什么凭据?

Google Cloud Application Default Credentials(ADC),不需要 Qirabot
账号或 API key。决策引擎在你自己的进程内运行,直接调用你项目里的
Google Vertex AI:把 `GOOGLE_APPLICATION_CREDENTIALS` 指向服务账号
JSON,或执行一次 `gcloud auth application-default login`(GCE 上自动
使用元数据服务器)。模型通过 `Qirabot(model="{provider}/{model}")` 或
`QIRA_MODEL` 选择,见[配置](/zh/advanced/configuration#模型与语言)。
`qirabot doctor` 可以校验整套配置。

## 哪些调用会请求模型,哪些不会?

走 AI 的调用会把截图发到你的 Vertex AI 端点(在那里消耗 token):
`ai()`、`extract`、`verify`、`wait_for`,以及 AI 定位的动作(带元素描述
的 `click`、`type_text`、`double_click`)。不经过 AI 的直接动作没有任何
开销:`navigate`、`go_back`、`close_tab`、`scroll`、`press_key`、
`screenshot`、`launch_app`、空 locate 的 `type_text`、不带 locate 的
`mouse_up`。[API 参考](/zh/reference/api)中标注了"无 AI"。模型用量由
Google Cloud 在你的项目上计费;每个结果对象都带 token 计数。

## 怎么把 token 账单降下来?

每步最大的固定成本是工具 schema,不是截图,所以 `exclude_tools` 通常是
第一笔立竿见影的收益;其次是把 `media_resolution` 从 `high` 降到
`medium`,以及把流程里已知的部分改用确定性步骤而不是 `ai()`。注意
`screenshot_quality` **不是**成本杠杆:图像 token 只取决于分辨率档位。
完整拆解见[控制成本](/zh/advanced/cost)。

## 哪些数据会离开我的机器?

截图、指令文本和步骤元数据。它们直接从你的机器发往你配置的模型端点
(Google Vertex AI,在你自己的 Google Cloud 项目里),不经过任何
Qirabot 服务器。代码、cookie、凭据都留在本地;动作在你的机器上执行。
详见[数据与隐私](/zh/reference/privacy)。

## 录屏为什么是黑的?

- **Windows 且 `record_window=True`**:默认模式是抓桌面再裁剪到窗口,
  游戏能正常录上,但最小化的窗口录不到东西——请保持窗口可见且不要移动。
  若设了 `QIRA_RECORD_WINDOW_NATIVE=1`,旧的 `gdigrab` 模式对 GPU 合成
  (游戏)窗口同样会录出黑帧。
- **macOS**:给终端/IDE 授予"屏幕录制"权限。

录屏是尽力而为:缺 ffmpeg 或权限被拒只警告,不会让任务失败;排查可以看运行
目录里的 `recording.ffmpeg.log`。详见[报告与录屏](/zh/advanced/reports)。

## 浏览器为什么自动变成 headless 了?

在没有显示器的机器上(无 `DISPLAY`),`bot.open()` 和 CLI 会自动降级为
headless 并给出警告。显式传 `--headless` 可以让它无条件生效。

## 遇到 `MissingDependencyError` 怎么办?

某个可选后端依赖未安装。错误消息里会按 qirabot 当前所处的环境给出确切的
安装命令——在 uv tool 环境里是 `uv tool install --force`,在项目环境里则是
普通的安装命令。extras 清单见[安装](/zh/guide/installation)。

## 脚本在步骤之间长时间等待,运行会超时吗?

不会。引擎在你自己的进程内运行,没有需要保活的服务端会话,`bot.*`
调用之间等多久都安全。详见
[配置](/zh/advanced/configuration#运行生命周期)。

## Android 上能输入中文和 emoji 吗?

能,直接调用 `bot.type_text(...)` 即可,不需要额外配置。超出 ASCII 的输入通过内置的
ADBKeyboard 输入法完成,按需安装、用完自动切回。见
[Android](/zh/backends/android)。

## 必须重写我现有的 Playwright / Selenium / Appium 套件吗?

不用。把你现有的 `page` 或 `driver` 作为目标传入,只在选择器难搞的地方
加 AI 步骤,见 [Playwright](/zh/frameworks/playwright)、
[Selenium](/zh/frameworks/selenium)、[Appium](/zh/frameworks/appium)、
[pytest](/zh/frameworks/pytest) 各集成指南。

## 我从 Airtest 迁移过来

内置设备后端是即插即用的替代
(`connect_device(...)` → `AdbDevice` / `WdaClient` / `Window`),参考
adapter 能让你的 airtest 脚本原样运行。见
[从 Airtest 迁移](/zh/backends/custom-adapters#从-airtest-迁移)。
