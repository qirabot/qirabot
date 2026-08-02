---
layout: home
title: Qirabot — AI 视觉驱动的跨端 GUI 自动化(浏览器/手机/桌面/游戏)
description: 基于多模态 AI 视觉的跨平台 GUI 自动化。用自然语言描述界面元素——无需 DOM、无需选择器。Python SDK 与 CLI,覆盖 Chrome、Android、iOS、Windows 与游戏。

hero:
  name: Qirabot
  text: AI 视觉驱动的 GUI 自动化
  tagline: 基于像素驱动浏览器、手机 App、桌面与游戏——无需 DOM、无需选择器。用自然语言描述目标,AI 看屏、决策、执行。
  actions:
    - theme: brand
      text: 快速开始
      link: /zh/guide/quickstart
    - theme: alt
      text: 安装
      link: /zh/guide/installation
    - theme: alt
      text: GitHub
      link: https://github.com/qirabot/qirabot-python

features:
  - title: 浏览器自动化
    details: 底层为 Playwright——bot.open() 自动启动 Chromium,也可通过 CDP 接管已运行的 Chrome。支持 headless、持久化 profile、远程浏览器池。
    link: /zh/backends/browser
  - title: Android 免 Appium
    details: adb 直连——无需服务器,设备端零安装。截图、点击、滑动、输入(含中文和 emoji),adb 能看到的设备都能驱动。
    link: /zh/backends/android
  - title: iOS 免 Appium
    details: 通过 HTTP 直连真机上的 WebDriverAgent。无需 Appium 服务器、无需额外包——只要 iproxy 和一个 WDA 会话。
    link: /zh/backends/ios
  - title: Windows 窗口与游戏
    details: 按标题或 HWND 绑定单个窗口。输入为 DirectInput 扫描码——游戏真正轮询的层级,虚拟键自动化无法触达。可驱动 Unity、Unreal 与原生应用。
    link: /zh/backends/windows-games
  - title: 挂载现有框架
    details: 已在用 Playwright、Selenium、Appium 或 pyautogui?直接传入你的 page/driver 对象,在现有套件中注入 AI 步骤——无需重写。
    link: /zh/backends/custom-adapters
  - title: 一套 API 全端通用
    details: bot.ai() 自主完成整个任务;bot.click() / extract() / verify() 提供 AI 定位的确定性步骤。所有平台调用方式一致。
    link: /zh/guide/quickstart
---

## Qirabot 是什么?

Qirabot 是一个**基于视觉的 GUI 自动化** Python SDK 与 CLI。它不查询 DOM
或无障碍树,而是直接看屏幕上的像素:多模态 AI 模型读取截图,找到你用自然
语言描述的元素,返回坐标由客户端执行操作。因此它能覆盖传统框架无法触及的
界面——canvas 渲染的 UI、原生手机 App、桌面应用,以及完整的 3D 游戏。

决策引擎在 SDK 内本地运行:截图直接从你的机器发往你自己在 Google Vertex
AI 上配置的模型端点——没有 Qirabot 账号、没有 API key、没有中间服务器。

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://www.wikipedia.org")

result = bot.ai(page, "搜索 SpaceX 并提取词条的第一句话")
print(result.output)

bot.close()
```

两种驱动方式:

- **自主任务** —— `bot.ai(page, "完成整个任务")`:AI 看屏、决定下一步
  动作,循环执行直到目标达成。
- **确定性步骤** —— `bot.click(page, "登录按钮")`、`bot.extract(...)`、
  `bot.verify(...)`:控制流在你手里,AI 视觉负责定位每个元素。没有 XPath、
  没有 CSS 选择器,布局变化也不会失效。

每次运行都会生成带逐步截图的 HTML 报告,`--record` 可录制视频。从
[快速开始](/zh/guide/quickstart) 入手,或直接跳到你要自动化的平台:
[浏览器](/zh/backends/browser) · [Android](/zh/backends/android) ·
[iOS](/zh/backends/ios) · [Windows 与游戏](/zh/backends/windows-games) ·
[桌面](/zh/backends/desktop)。
