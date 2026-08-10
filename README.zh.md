# Qirabot

[English](README.md) | 简体中文

跨平台 GUI 自动化，由多模态 AI 视觉驱动。像人一样识别屏幕画面并直接操作浏览器、手机 App、整个桌面与游戏——无需 DOM、无需选择器——覆盖 Playwright / Selenium / Appium 这类框架无法触及的场景。

既可独立运行（`bot.open()` 自动启动浏览器；Android / iOS / Windows 窗口后端内置，零额外依赖），也可接入你现有的 Playwright / Selenium / Appium / pyautogui 会话、嵌入 pytest 测试套件，或按 HWND 绑定窗口驱动 Unity / Unreal / 原生桌面游戏。所有平台共用同一套 API。

决策引擎在你自己的进程内运行：每张截图直接从你的机器发往你在 Google Vertex AI 上自行配置的视觉模型，使用你自己的 Google Cloud 凭据认证，费用由 Google 计收。

**📖 完整文档：[qirabot.com/docs/zh](https://qirabot.com/docs/zh/)**

## 效果演示

https://github.com/user-attachments/assets/649ea80c-63e7-4c85-9ee8-3c8fe17e5ef4

**《梦幻西游》手游：从创号自动玩到 15 级** — iOS 真机。
整个任务指令只有一句话：*"这是梦幻西游手游，你的任务是创建角色，然后完成新手流程，能跳过的尽可能跳过"*。
上方为单次未剪辑运行的精华片段：[完整 5:50 视频](https://qirabot.com/docs/zh/demos.html#mhxy_zero_to_15) ·
[脚本](examples/game/ios_mmorpg.py)

更多真实、未剪辑的运行记录——AI 全程只看屏幕画面。点击封面观看（[全部 demo →](https://qirabot.com/docs/zh/demos.html)）：

<table>
  <tr>
    <td align="center" width="33%">
      <a href="https://qirabot.com/docs/zh/demos.html#afk_journey_tutorial"><img src="https://assets.qirabot.com/demos/afk_journey_tutorial.poster.webp" alt="《剑与远征：启程》创号通关新手教程，进入大世界"></a>
      <br><b>《剑与远征：启程》创号通关新手教程，进入大世界</b> — iOS 真机
    </td>
    <td align="center" width="33%">
      <a href="https://qirabot.com/docs/zh/demos.html#lichess_play_chess"><img src="https://assets.qirabot.com/demos/lichess_play_chess.poster.webp" alt="在 lichess.org 上对弈国际象棋"></a>
      <br><b>在 lichess.org 上对弈国际象棋</b> — Android 真机
    </td>
    <td align="center" width="33%">
      <a href="https://qirabot.com/docs/zh/demos.html#tile_match_game"><img src="https://assets.qirabot.com/demos/tile_match_game.poster.webp" alt="自主通关水果连连消手游"></a>
      <br><b>自主通关水果连连消手游</b> — Android 真机
    </td>
  </tr>
</table>

## 安装

一行命令——自动安装 [uv](https://docs.astral.sh/uv/)、qirabot（隔离环境，不改动系统 Python）和 Chromium，无需预装 Python：

```bash
# macOS / Linux
curl -LsSf https://qirabot.com/install | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://qirabot.com/install.ps1 | iex"
```

需要驱动设备而不是浏览器？Android（adb）、iOS（WDA）、Windows 单窗口后端均内置于核心包：

```bash
uv tool install qirabot        # Android + iOS + Windows 窗口；零额外依赖
```

pip、虚拟环境、各框架 extras 与故障排查见[安装指南](https://qirabot.com/docs/zh/guide/installation.html)。安装完成后运行 `qirabot doctor`，它会报告已安装与缺失的组件（并给出对应的修复命令），以及 Google Cloud 凭据能否正常解析。

## 快速上手

Qirabot 使用你自己的 Google Cloud 凭据调用 Google Vertex AI 上的视觉模型。先配置一次 Application Default Credentials（ADC）：

```bash
gcloud auth application-default login
# 服务账号 / CI 场景：
# export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
```

不想装 gcloud？`gemini-vertex` 系列模型可以改用
[Vertex AI API key](https://cloud.google.com/vertex-ai/generative-ai/docs/start/api-keys)
认证——注意是 Google Cloud 的 API key，不是 AI Studio 的 key。仅支持
Google 自家模型，且只走全局端点：

```bash
export QIRA_VERTEX_API_KEY="..."   # 或 Qirabot(vertex_api_key=...)
```

完全不想碰 Google Cloud？`gemini` provider 直接调用
[Gemini Developer API](https://ai.google.dev/gemini-api/docs/api-key)，
用 AI Studio 的 API key 认证：

```bash
export QIRA_GEMINI_API_KEY="..."   # 或 GEMINI_API_KEY、Qirabot(gemini_api_key=...)
export QIRA_MODEL="gemini/gemini-3.6-flash"
```

模型按 `{provider}/{model}` 格式指定，provider 为 `gemini-vertex`、`gemini` 之一，默认 `gemini-vertex/gemini-3.6-flash`；如果凭据本身不带项目，再指定 Google Cloud 项目：

```bash
export QIRA_MODEL="gemini-vertex/gemini-3.6-flash"   # 或 Qirabot(model=...)
export QIRA_VERTEX_PROJECT="my-gcp-project"                # 或 Qirabot(vertex_project=...)
```

`qirabot models` 会列出全部 provider 并检测凭据是否可用。然后把任务交给 AI。下面是一次真实运行的完整输出——AI 自己导航、滚动、记录数据，最后按要求整理成表格：

```text
$ qirabot browser "百度热搜中10条热点新闻，以表格形式返回" -l zh
Run: local-d2631017
[1/20] navigate
        └ 打开百度热搜页面
[2/20] save_note
        └ 保存当前屏幕可见的百度热搜前几名数据
[3/20] scroll  down 600
        └ 向下滚动以查看更多热搜新闻
[4/20] save_note
        └ 保存第5到第10名的热搜数据
Done: 以下是百度热搜中的热点新闻：

| 排名 | 热搜话题 | 热搜指数 |
| --- | --- | --- |
| 1 | 《功夫女足》换片源 | 7809330 |
| 2 | 富士康打工妹逆袭成中国女首富 | 7714556 |
| 3 | 二季度GDP增长4.3% 怎么看 | 7618975 |
| 4 | 国科大本科录取通知书001号签收 | 7523307 |
| 5 | 克罗地亚正式致函FIFA抗议 | 7427059 |
| 6 | 演员许鹏自曝已返乡卖菜 | 7328813 |
| 7 | 家长月薪3500元请大学生暑假带娃 | 7236804 |
| 8 | 有200台小米设备的“米粉”心凉了 | 7140192 |
| 9 | 安理会通过决议 中俄投了弃权票 | 7040362 |
| 10 | 世界杯：英格兰vs阿根廷 | 6952280 |
```

每次运行都会生成带逐步截图的 HTML 报告，加 `--record` 还能录下整个过程的视频。

## 实战场景：社交媒体运营

以 B 站为例，把日常互动交给一句自然语言指令。`--user-data-dir` 指定一个持久化浏览器 profile：先用 `open-browser` 手动登录一次——不跑 AI 任务、不花 token，扫码登录后关闭窗口，登录态就保存在 profile 里：

```bash
qirabot open-browser --user-data-dir ~/.automation --url bilibili.com
```

之后所有传入同一个 profile 的运行都全程无人值守。以下是真实运行输出，仅省略了部分中间步骤：

```text
$ qirabot browser --user-data-dir ~/.automation "给B站影视飓风最新发布的第二个视频点赞和评论" -l zh
Run: local-f123a9a8
[1/20] navigate
        └ 打开B站官网
        ⋯（略 4 步：搜索、进入主页、打开投稿列表）
[6/20] click
        └ 点击最新发布的第二个视频“去了一趟得克萨斯州。”
[7/20] click
        └ 点击点赞按钮给视频点赞
        ⋯（略 1 步：滚动到评论区）
[9/20] type_text  ← "视频拍得太棒了！支持影视飓风！"
        └ 在评论输入框中输入评论内容并发送
[10/20] click
        └ 点击“发布”按钮发送评论
Done: 已成功给B站影视飓风最新发布的第二个视频点赞并发表评论。
```

凡是"打开网页、看着屏幕操作"的运营流程——发布内容、回复评论、巡检数据——都可以这样写成一句话，配合 crontab 或 CI 定时跑。

## Python SDK

CLI 背后是同一个引擎。用 Python 调用 `bot.ai()`，AI 同样观察屏幕、决策下一步动作、循环执行直到任务完成——区别是结果直接回到你的代码里，每一步动作还能通过 `on_step` 回调实时拿到：

```python
from qirabot import Qirabot, StepResult

bot = Qirabot()
page = bot.open("https://top.baidu.com/board?tab=realtime")

def on_step(step: StepResult) -> None:
    label = "done" if step.finished else step.action_type
    print(f"  step {step.step}: {label} {step.params}")

result = bot.ai(page, "提取百度热搜中的 10 条热点新闻，以表格形式返回", on_step=on_step)
print(f"Success: {result.success}")
print(f"Result: {result.output}")

bot.close()
```

想自己控制每一步？同样的自然语言定位也能按单步调用——`bot.click(page, "登录按钮")`、`bot.extract(...)`、`bot.verify(...)`——流程由你的代码掌控。只想要坐标？`x, y = bot.locate(page, "确定按钮")` 只定位不执行，坐标可直接喂给你自己的框架调用。

也无需重写任何代码：将现有的 `page` / `driver` / 设备对象直接传入，即可在原有选择器脚本中混用 AI 步骤（视觉断言、动态控件、逐步脚本化过于繁琐的流程）——Playwright / Selenium / Appium / pyautogui 及内置设备后端均适用，详见[框架集成文档](https://qirabot.com/docs/zh/frameworks/playwright.html)。

## 配合 AI Agent 使用

Qirabot 提供符合 [Agent Skills 开放标准](https://agentskills.io)的预置 [skill](https://qirabot.com/docs/zh/guide/agents.html)，可被 Claude Code、Codex、Cursor 等 agent 加载。skill 内置 preflight 环境检查脚本、与 SDK 版本对齐的精简 API 参考，以及各平台起步模板。agent 接到自然语言的自动化目标后，先校验环境，再按任务形态选择执行路径——一次性任务直接调 CLI，需要分支逻辑或消费返回值时编写 SDK 脚本——最后核对运行结果。

任意兼容 Agent Skills 的工具都可以安装 pip 包内置的副本，与本机 SDK 版本严格一致：

```bash
qirabot skill install agents        # 或 claude / codex / cursor / --dir <path>
```

Claude Code 用户推荐通过 plugin marketplace 安装（自动更新）：

```text
/plugin marketplace add qirabot/claude-plugins
/plugin install qirabot@qirabot
```

也可以通过 [skills CLI](https://github.com/vercel-labs/skills)，从仓库 `main` 分支安装：

```bash
npx skills add qirabot/qirabot
```

skill 的 API 参考与模板在 CI 中与 SDK 实时接口做防漂移校验（`tests/test_skill.py`）。详见 [plugins/qirabot/README.md](plugins/qirabot/README.md)。

## 领域知识：把你的规则教给 AI

模型懂得怎么操作界面，但不懂你游戏里的道具名、你团队的业务术语。把参考文本挂载到任务上，AI 每一步决策都会参考它。CLI 里用 `-k` 传文件，可重复，合计 32KB：

```bash
qirabot browser "在商城买 10 瓶体力药水" -k game-rules.md -k gm-policy.md
```

Python 里 `knowledge` 接受字面文本、UTF-8 文件，或两者混合的列表：

```python
result = bot.ai(
    device,
    "完成所有日常任务",
    knowledge=[Path("game-rules.md"), "GM 命令每局只能使用一次"],
)
```

知识按调用挂载：下一次 `bot.ai()` 从零开始，长流程的每个阶段只带自己需要的知识。两个有意为之的限制：不支持 URL——远程内容请自行下载，让鉴权和失败处理留在你的代码里；知识只能*引导*决策——"每局一次"这类硬规则应该写进自定义工具的代码里（见下一节），那里才能真正强制执行。

## 自定义工具：让 AI 调用你的代码

任务执行中，AI 不只会点击和输入。`custom_tools` 可以把普通 Python 函数注册为模型可调用的工具——调内部 API、查数据库、从邮箱取验证码、造测试数据，或在遇到 CAPTCHA 时暂停等人工处理。工具的名称、描述和参数会自动从函数本身提取：

```python
def gm_command(command: str) -> str:
    """向游戏 GM 后台发送命令并返回结果。
    可用命令：add_energy <数量>、add_gold <数量>"""
    return requests.post(GM_URL, json={"cmd": command}, timeout=10).text

result = bot.ai(
    device,
    "完成所有日常任务。如果弹出体力不足的提示，"
    "就用 gm_command 加 100 体力后继续",
    custom_tools=[gm_command],
)
```

工具在你自己的进程内执行（qirabot 的其余一切也是如此）——模型只能看到工具的名称、描述、参数和返回值，接触不到你的接口和凭据——返回值会作为模型下一步的观察结果。过去需要一整页胶水代码串联的跨系统流程（UI 操作、后端调用、人工介入），现在一句指令就能覆盖。更多细节（schema、错误处理、裁剪内置工具）见[AI 任务与自定义工具](https://qirabot.com/docs/zh/advanced/ai-tasks.html)；可运行示例：[custom_tool_gm.py](examples/game/custom_tool_gm.py) · [06_human_in_the_loop.py](examples/automation/06_human_in_the_loop.py)。

这两个参数组合起来，也能复用你已经为别的 agent 维护的指令文件——agent skill（`SKILL.md`）、runbook：用 `knowledge=` 挂载文件；文件里的 CLI 步骤，注册一个带白名单的 `run_command` 自定义工具来执行（内置工具刻意只含 GUI 动作）。完整模式与参考实现见[复用已有 skill](https://qirabot.com/docs/zh/advanced/ai-tasks.html#复用已有-skill)。

## 进度悬浮窗

CLI 的每个任务命令都会在屏幕右下角显示一个置顶小窗：当前指令、每一步的动作
和模型思考、以及最终的 ✓/✗ 结果。这个窗口**不会被截图捕获**且点击穿透——
既不会出现在 bot 自己的截图里，也不会挡住要点给下层应用的点击。用
`--no-overlay` 关闭；SDK 中一个参数：

```python
bot = Qirabot(overlay=True)   # 每次 bot.ai() 的进度都会显示到窗口
```

当任务驱动的是**真实键鼠**（桌面后端）时，屏幕四边还会亮起缓慢呼吸的
琥珀色光晕——"机器正被接管，请勿操作"的信号——此时**长按 ESC 约一秒
即可中止任务**：bot 松开所有按住的键，任务被记录为**已取消（cancelled）**
而非失败。远程协议目标（浏览器、Android、iOS）两者都不点亮：那些场景下
你的键鼠依然是你的。

standalone 的 `Overlay` 还能把你自己脚本的阶段和 bot 步骤显示到同一个窗口。
更多细节——中止语义（`user_abort`、`clear_user_abort()`）、standalone
API、平台说明与限制：[进度悬浮窗与急停](https://qirabot.com/docs/zh/advanced/overlay.html)。
可运行示例：[overlay_progress.py](examples/desktop/overlay_progress.py)。

## 文档

| 主题 | |
|---|---|
| 入门 | [安装](https://qirabot.com/docs/zh/guide/installation.html) · [快速上手](https://qirabot.com/docs/zh/guide/quickstart.html) · [CLI 参考](https://qirabot.com/docs/zh/guide/cli.html) |
| 支持平台 | [浏览器](https://qirabot.com/docs/zh/backends/browser.html) · [Android（adb，免 Appium）](https://qirabot.com/docs/zh/backends/android.html) · [iOS（WDA，免 Appium）](https://qirabot.com/docs/zh/backends/ios.html) · [Windows 与游戏（DirectInput）](https://qirabot.com/docs/zh/backends/windows-games.html) · [桌面](https://qirabot.com/docs/zh/backends/desktop.html) · [自定义 Adapter](https://qirabot.com/docs/zh/backends/custom-adapters.html) |
| 框架集成 | [Playwright](https://qirabot.com/docs/zh/frameworks/playwright.html) · [Selenium](https://qirabot.com/docs/zh/frameworks/selenium.html) · [Appium](https://qirabot.com/docs/zh/frameworks/appium.html) · [pytest](https://qirabot.com/docs/zh/frameworks/pytest.html) |
| 进阶 | [AI 任务与自定义工具](https://qirabot.com/docs/zh/advanced/ai-tasks.html) · [进度悬浮窗与急停](https://qirabot.com/docs/zh/advanced/overlay.html) · [报告与录屏](https://qirabot.com/docs/zh/advanced/reports.html) · [配置](https://qirabot.com/docs/zh/advanced/configuration.html) · [错误处理](https://qirabot.com/docs/zh/advanced/error-handling.html) |
| 参考 | [API——动作与平台矩阵](https://qirabot.com/docs/zh/reference/api.html) |

## 示例

可直接运行的示例在 [examples/](examples/)：pytest 集成（Playwright / Selenium / Appium / 桌面）、独立自动化脚本（爬取 / RPA / agent），以及游戏驱动（Windows 桌面游戏 + demo 视频背后的 iOS 手游脚本）。选择指南见 [examples/README.md](examples/README.md)。

## 许可证

MIT
