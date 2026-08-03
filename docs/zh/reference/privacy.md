---
title: 数据与隐私
description: Qirabot 具体向你自己的 Vertex AI 端点发送什么(截图、指令、步骤元数据)、什么永不离开你的机器(代码、cookie、凭据),以及只存本地的报告文件。不经过任何 Qirabot 服务。
---

# 数据与隐私

Qirabot 的决策引擎在 SDK 内本地运行:模型需要看到屏幕,除此之外什么都
不需要。截图直接从你的机器发往你自己 Google Cloud 项目下的 Vertex AI
端点。`qirabot` 包不向任何 Qirabot 服务发起网络请求——没有账号、没有
API key、没有服务端任务存储。本页明确说明哪些数据会经过网络。

## 发送到你的模型端点的内容

每个 AI 步骤发送给你配置的 Vertex AI 模型(在你自己的 Google Cloud
项目下):

- 绑定目标的**截图**(默认 JPEG、质量 80——`screenshot_format` /
  `screenshot_quality` 见[配置](/zh/advanced/configuration)),
- 你的**指令文本**(自然语言描述或任务),
- **步骤元数据**(动作类型、参数、耗时)。

模型从屏幕上提取的内容(`extract()` 的结果、`ai()` 的输出)由同一端点
生成并返回给你的进程,不会有其他任何一方收到。

## 什么永不离开你的机器

- **你的代码。** 模型只返回坐标和决策;动作通过你的框架或 adapter 在
  本地执行。
- **Cookie、凭据、会话状态。** Qirabot 驱动你的浏览器或设备,不读取也
  不传输它们的存储。
- **自定义工具。** 通过 `custom_tools` 传入的函数在本地运行——你的接口
  地址、token、数据库,模型端点一概看不到,只有工具的字符串返回值会反馈
  给模型。见 [AI 任务与自定义工具](/zh/advanced/ai-tasks)。

## 什么只存本地

所有运行数据——名称、状态、步骤和步骤截图——都留在本地磁盘上。
[HTML 报告](/zh/advanced/reports)(`report.html`、全分辨率
`screenshots/`、`recording.mp4`)写入你机器上的 `./qira_runs/`,完全自
包含、不发起任何网络请求。`report=False` 可整体关闭。设置
`QIRA_ENGINE_TRACE=<目录>`(调试用途)时,引擎还会向该本地目录写入每次
模型调用一条的 JSONL 记录及对应截图。

## 传输

所有模型流量走 HTTPS 到你配置的 Vertex AI 端点,以你的 Google Cloud
凭据(Application Default Credentials)认证。没有其他网络目的地。
