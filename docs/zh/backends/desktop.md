---
title: AI 视觉桌面自动化(pyautogui)
description: 在 macOS、Windows、Linux 上用 AI 视觉自动化任意桌面应用——启动应用、按描述点击、按住按键、录制屏幕,基于 pyautogui。
---

# 桌面(pyautogui)

桌面后端通过 pyautogui 驱动 macOS / Windows / Linux 的整个屏幕,用 AI
视觉取代找坐标和模板图片的工作。你描述元素,AI 在截图上找到它,点击就
落在正确位置。

需要 `desktop` extra:`pip install "qirabot[desktop]"`。

最快的验证方式是 CLI:

```bash
qirabot desktop "新建一条标题为 Groceries 的备忘录" --app Notes
```

同样的事在 Python 里:

```python
import pyautogui
from qirabot import Qirabot

bot = Qirabot(task_name="wechat")

bot.launch_app("WeChat")              # macOS 应用名(或 bundle id)
bot.ai(pyautogui, "在微信里给 honey 发一句 hello")
bot.close()
```

这里的目标是 `pyautogui` 模块本身:桌面上没有 page 或 driver 对象,
传入模块就表示“驱动整个屏幕”。(每个调用都这样接收目标;可接受的
目标类型全表见[自定义 Adapter 与挂载](/zh/backends/custom-adapters),
用 `bind()` 可省去重复传参。)

## 启动应用

pyautogui 能移动鼠标但打不开应用。`launch_app` 调用操作系统,让桌面运行
从确定的应用开始:

```python
bot.launch_app("WeChat")             # macOS:应用名或 bundle id
# launch_app("notepad")              # Windows:exe 路径、注册名或 UWP AppUserModelID
# launch_app("/path/to/app", wait=3) # 等窗口出现的秒数(默认 2)
```

各系统的启动机制见
[API 参考](/zh/reference/api#启动桌面应用-无-ai)。

## 桌面独有的输入原语

桌面后端(pyautogui 和 [Windows 窗口后端](/zh/backends/windows-games))
支持 web/移动端没有的输入形态:

```python
bot.press_key(pyautogui, "w", duration_seconds=2)        # 按住按键
bot.click(pyautogui, "文件行", modifier="ctrl+shift")     # 修饰键点击
bot.key_down(pyautogui, "shift")                          # 按下/释放拆分
bot.mouse_down(pyautogui, "滑块手柄")                     # 按住拖拽
bot.mouse_up(pyautogui)                                   # 在当前光标处释放
```

`ai()` 运行结束和 `close()` 时会自动释放仍按住的输入。
每个原语的确切语义见
[平台支持矩阵](/zh/reference/api#平台支持矩阵)。

## 录屏

`Qirabot(record=True)` 用 ffmpeg 录制整个运行过程的全屏画面,
`recording.mp4` 嵌入 HTML 报告。macOS:给终端/IDE 授予"屏幕录制"权限,
多显示器用 `QIRA_SCREEN_INDEX` 选择。录制是尽力而为的:缺 ffmpeg 只会
警告,不会导致任务失败。

## 两个桌面后端怎么选

| | pyautogui 后端 | [Window 后端](/zh/backends/windows-games) |
|---|---|---|
| 系统 | macOS / Windows / Linux | 仅 Windows |
| 范围 | 全屏 | 单个窗口(标题正则 / HWND) |
| 输入层级 | 虚拟键 | DirectInput 扫描码(游戏可读) |
| 安装 | `qirabot[desktop]` | 内置 |

经验法则:自动化游戏,或想在 Windows 上把操作隔离在单个窗口里,用
Window 后端;其余桌面场景用 pyautogui。
