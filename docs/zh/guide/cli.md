---
title: CLI 参考
description: 在命令行运行自然语言 GUI 自动化任务——browser、android、ios、desktop 四个子命令,录屏、报告与脚本友好的退出码。
---

# CLI 参考

`qirabot` 命令随核心包安装,不写 Python 就能端到端运行任务。`android`、
`ios` 和 `desktop --window-title/--hwnd` 走内置后端,不需要 extras。只有
`browser`(`qirabot[browser]`)、全屏 `desktop`(`qirabot[desktop]`)和
Appium 引擎(`qirabot[appium]`)需要对应 extra。

```bash
# 浏览器(需要 qirabot[browser] + `playwright install chromium`)
qirabot browser "搜索 SpaceX 并提取词条的第一句话" --url wikipedia.org

# 浏览器——headless/视口;持久化 profile;或经 CDP 接管已运行的 Chrome
qirabot browser "..." --headless --viewport 1920x1080
qirabot browser "..." --user-data-dir ~/.qira-profile --channel chrome
qirabot browser "..." --cdp-url http://localhost:9222

# Android——adb 直连(内置;只需 adb 二进制,无需服务器)
qirabot android "打开设置并开启飞行模式"
qirabot android "..." -d emulator-5554 --app-package com.android.settings

# iOS——直连 WebDriverAgent(内置;WDA 需运行在 :8100)
qirabot ios "在微信里给 Alice 发一句 hi" --bundle-id com.tencent.xin

# 两者也可改走 Appium 服务器(需要 qirabot[appium])
qirabot android "..." --appium-url http://localhost:4723
qirabot ios "..." --device "iPhone 15"   # 仅模拟器(选择 Appium 引擎)

# 桌面(pyautogui,需要 qirabot[desktop])
qirabot desktop "新建一条标题为 Groceries 的备忘录" --app Notes

# 绑定单个 Windows 窗口(内置)——DirectInput 扫描码输入
qirabot desktop "打开背包并列出所有物品" --window-title "Genshin"
qirabot desktop "..." --hwnd 132456

# 为本次运行挂载领域知识——游戏规则、业务术语(合计 32KB)
qirabot browser "在商城买 10 瓶体力药水" -k game-rules.md -k gm-policy.md

# 环境自检——Python、Google Cloud 凭据(ADC)、各后端依赖
qirabot doctor

# 模型总览——Vertex provider、默认模型、凭据状态
qirabot models
```

## 命令一览

| 命令 | 用途 |
|---|---|
| `browser 指令` | 在本地浏览器运行 AI 任务([浏览器后端](/zh/backends/browser)) |
| `android 指令` | 在 Android 设备运行 AI 任务([adb 直连](/zh/backends/android),内置;`--appium-url` 走 Appium) |
| `ios 指令` | 在 iOS 设备运行 AI 任务([WDA 直连](/zh/backends/ios),内置;`--appium-url`/`--device` 走 Appium) |
| `desktop 指令` | 在[桌面](/zh/backends/desktop)运行 AI 任务(pyautogui;`--window-title`/`--hwnd` 绑定[单个 Windows 窗口](/zh/backends/windows-games),内置) |
| `install-browser` | 一次性下载浏览器后端所需的 Chromium |
| `open-browser` | 打开浏览器手动登录网站;登录态保存在 `--user-data-dir`,供后续运行复用 |
| `doctor` | 检查 Python、Google Cloud 凭据(ADC + 项目)与各后端依赖 |
| `models` | 打印内置的 Vertex provider 及其默认模型、本次会话的默认模型,以及所配置的认证方式(API key 和/或 ADC)能否解析 |
| `skill install [AGENT]` | 把自带的 [Agent Skill](/zh/guide/agents) 装进 AI agent 的 skills 目录 |
| `skill uninstall [AGENT]` | 移除 `skill install` 装的 skill |
| `skill list` | 列出已知 skills 目录与已安装的 skill 版本 |

## 全局选项

全局选项要写在子命令之前,用于配置 Vertex AI 连接:

```bash
qirabot --vertex-project my-gcp-project --vertex-location global browser "..."
```

项目的解析顺序:`--vertex-project` 参数 > `QIRA_VERTEX_PROJECT` 环境变量
> `GOOGLE_CLOUD_PROJECT` > ADC 凭据自身的项目 id。位置(location):
`--vertex-location` > `QIRA_VERTEX_LOCATION` > `GOOGLE_CLOUD_LOCATION` >
`global`。另有 `--version`。

`--vertex-api-key`(或 `QIRA_VERTEX_API_KEY`)使用
[Vertex AI API key](https://cloud.google.com/vertex-ai/generative-ai/docs/start/api-keys)
认证来代替 ADC,不用安装和配置 gcloud。注意这是 Google Cloud 的 API
key,不是 AI Studio 的 key;仅支持 `gemini-vertex` 系列模型,固定走全局
端点,并优先于 `--vertex-project`/`--vertex-location`。

`--gemini-api-key`(或 `QIRA_GEMINI_API_KEY` / `GEMINI_API_KEY`)是
`gemini` provider 用的
[AI Studio API key](https://ai.google.dev/gemini-api/docs/api-key)。该
provider 直接调用 Gemini Developer API 而非 Vertex,完全不涉及 Google
Cloud(`-m gemini/gemini-3.6-flash`)。

模型是任务命令上的选项(`-m/--model`,见下),解析顺序:`-m` 参数 >
`QIRA_MODEL` 环境变量 > 内置默认值
`gemini-vertex/gemini-3.6-flash`。只写 provider 名会选用该
provider 的默认模型(`gemini` →
`gemini-3.6-flash`)。

## 退出码

退出码是为脚本设计的:`0` 任务成功,`1` 任务失败或出错,`130` Ctrl+C
中断。因此 `qirabot browser "..." && next-step` 只在成功时继续。

## 机器可读输出

`--output-format json` 让 stdout 只输出一个 JSON 结果对象(人类可读输出被
抑制;退出码语义不变):

```json
{
  "type": "result",
  "success": true,
  "status": "completed",
  "output": "已登录并进入仪表盘",
  "task_id": "local-1a2b3c4d",
  "usage": {
    "ai_steps": 6,
    "input_tokens": 48210,
    "output_tokens": 3120,
    "thinking_tokens": 0,
    "cache_read_tokens": 12040,
    "cache_write_tokens": 0,
    "step_duration_ms": 41830,
    "llm_decision_duration_ms": 28510,
    "total_tokens": 63370
  },
  "report": "qira_runs/2026-08-03/143012-1a2b3c4d/report.html"
}
```

`status` 取值 `completed` / `goal_failed` / `max_steps` / `error` /
`cancelled`,与 SDK `RunResult.status` 相同,外加 Ctrl+C 和 ESC 中止对应的
`cancelled`。`success` 仅在 `completed` 时为 `true`。关闭报告时 `report` 为
`null`;报告文件在进程退出时写出,应在 CLI 返回后再读取。

`--output-format stream-json` 输出 NDJSON,每行一个 JSON 对象、逐步 flush,
适合实时监控运行的上层工具:

```
{"type": "start", "task_id": "local-1a2b3c4d", "max_steps": 20}
{"type": "step", "step": 1, "action_type": "click", "params": {"locate": "登录按钮"}, "decision": "...", ...}
{"type": "step", "step": 2, "action_type": "input", ...}
{"type": "result", "success": true, ...}
```

`step` 行的字段与 SDK 的 `StepResult` 一致(`step`、`action_type`、
`params`、`decision`、`output`、`finished` 及单步 token/耗时计数);末尾的
`result` 行与 `json` 格式的对象相同。中断运行的错误(包括设备不可达等
setup 阶段失败)同样以 `result` 对象结束(`status: "error"`),消费方
总能读到一条终止行。

## 通用运行选项

`browser` / `android` / `ios` / `desktop` 均支持:

| 选项 | 默认值 | 作用 |
|---|---|---|
| `-n, --name` | 从指令推导 | HTML 报告中显示的运行名 |
| `-m, --model` | `QIRA_MODEL`,否则 `gemini-vertex/gemini-3.6-flash` | 模型,格式 `{provider}/{model}`,provider 为 `gemini-vertex` / `gemini` 之一(见[配置](/zh/advanced/configuration)) |
| `--thinking-level` | 引擎默认 | 思考深度覆盖:`minimal` / `low` / `medium` / `high`(见[配置](/zh/advanced/configuration#思考深度)) |
| `--media-resolution` | `QIRA_MEDIA_RESOLUTION`,否则 `high` | 模型看到的截图精细度:`low` / `medium` / `high` / `ultra_high`(仅 Gemini);调低可减少每步的图像 token |
| `-l, --language` | 跟随指令语言 | 响应语言:语言标签(`zh`、`ja`、`de` 等)或任意语言名称 |
| `--max-steps` | `20` | AI 任务的步数预算 |
| `-k, --knowledge` | — | 任务期间供 AI 参考的知识文件(UTF-8 文本;可重复,合计 32KB)。规则与 `bot.ai(knowledge=...)` 一致:只收文件、不收 URL,远程内容请先自行下载 |
| `--report / --no-report` | 开 | 写 HTML 运行报告 |
| `--report-dir` | `./qira_runs/...` | 报告输出根目录(环境变量 `QIRA_REPORT_DIR`) |
| `--annotate / --no-annotate` | 开 | 在保存的截图上用十字线标注点击/输入坐标 |
| `--record` | 关 | 把运行录制为 `recording.mp4`(见下) |
| `--output-format` | `text` | `json` / `stream-json` 输出机器可读的 stdout(见[机器可读输出](#机器可读输出)) |

## 各命令专属选项

**`browser`**(详见[浏览器后端](/zh/backends/browser)):

| 选项 | 默认值 | 作用 |
|---|---|---|
| `-u, --url` | — | 要打开的 URL(省略则由 AI 自行导航) |
| `--headless` | 关 | headless 模式(无显示器时自动开启) |
| `--viewport` | `1280x800` | 视口,格式 `宽x高`(`WIDTHxHEIGHT`) |
| `--channel` | 自带的 Chromium | 使用已安装的浏览器:`chrome`、`msedge` 等 |
| `--user-data-dir` | — | 持久化 profile 目录(cookie/登录态跨运行保留) |
| `--browser-arg` | — | 额外的 Chromium 启动参数,可重复 |
| `--cdp-url` | — | 经 CDP 接管已运行的 Chrome;与上面四个选项互斥 |

**`android`**(详见 [Android 后端](/zh/backends/android)):

| 选项 | 默认值 | 作用 |
|---|---|---|
| `-d, --device` | 唯一已连接的设备 | `adb devices` 里的 adb serial |
| `--app-package` | — | 要启动的应用包名(如 `com.android.settings`) |
| `--app-activity` | — | 要启动的应用 activity |
| `--appium-url` | adb 直连,无服务器 | 传入即切换到 [Appium 引擎](/zh/frameworks/appium) |
| `--record` | 关 | 录制设备屏幕(adb screenrecord / Appium API) |

**`ios`**(详见 [iOS 后端](/zh/backends/ios)):

| 选项 | 默认值 | 作用 |
|---|---|---|
| `--wda-url` | `http://127.0.0.1:8100` | WebDriverAgent 地址,由它选择设备(USB 真机:`iproxy 8100 8100`) |
| `--bundle-id` | — | 要启动的应用 bundle id(如 `com.tencent.xin`) |
| `--device` | — | `xcrun simctl list devicetypes` 里的模拟器设备类型;传入即切换到 Appium 引擎,仅模拟器(无 `-d` 简写:切换引擎应显式写全) |
| `--appium-url` | WDA 直连,无服务器 | Appium 服务器地址(配合 `--device`) |
| `--record` | 关 | 录制设备屏幕(WDA MJPEG + ffmpeg / Appium API) |
| `--mjpeg-url` | `--wda-url` 主机的 9100 端口 | `--record` 的 MJPEG 流覆盖地址 |

**`desktop`**(详见[桌面](/zh/backends/desktop)与
[Windows 与游戏](/zh/backends/windows-games)):

| 选项 | 默认值 | 作用 |
|---|---|---|
| `--app` | — | 先启动/激活应用(macOS:名称或 bundle id;Windows:exe/注册名/UWP id;Linux:可执行文件) |
| `--app-wait` | `2.0` | `--app` 之后等窗口出现的秒数 |
| `--window-title` | — | 绑定标题匹配该正则的窗口(Windows 窗口后端) |
| `--hwnd` | — | 绑定窗口句柄,十进制(Windows 窗口后端) |
| `--ambiguous` | `error` | 多个窗口匹配 `--window-title` 时:`error` 报错并列出候选;`largest` 选面积最大的窗口 |

**`skill install [AGENT]`** 安装自带的
[Agent Skill](/zh/guide/agents)(SKILL.md、preflight 脚本、API 参考、起步
模板),版本与本机安装的 `qirabot` 严格一致。`AGENT` 可选 `agents`(Codex、Cursor、Gemini CLI
等工具共享的 `.agents/skills` 约定)、`claude`、`codex`、
`cursor`;其他工具用 `--dir PATH` 指定目录。`--project` 装进当前目录下的
项目级 skills 目录而不是用户级。`uv tool upgrade qirabot` 之后重跑即升级;
不是本命令创建的目录绝不覆盖,除非加 `--force`。Claude Code 用户仍推荐
plugin marketplace 安装(可自动更新)。`skill uninstall` 接受同样的目标
选项;`skill list` 查看各处安装状态。

`--record` 把 `recording.mp4` 存入运行目录并嵌入 HTML 报告。录制对象因
平台而异:

- `browser` / `desktop`:用 ffmpeg 录制宿主机屏幕(ffmpeg 需在 PATH)。
  绑定窗口时(`--window-title`/`--hwnd`)只录该窗口。
- `android`:录制设备屏幕。默认引擎用 `adb screenrecord`,Appium 引擎
  用其录屏 API。
- `ios`:录制设备屏幕。默认引擎用 WDA 的 MJPEG 流(需要 ffmpeg;USB
  真机还需 `iproxy 9100 9100`),Appium 引擎用其录屏 API。

录制机制、报告结构与音频采集见
[报告与录屏](/zh/advanced/reports)。运行同样遵循 SDK 的环境变量
(`QIRA_REPORT_DIR`、`QIRA_SETTLE_SECONDS`、`QIRA_RECORD*` 等),完整清单见
[配置](/zh/advanced/configuration)。
