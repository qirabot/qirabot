---
title: HTML 运行报告与录屏
description: Qirabot 每次运行自动生成带逐步截图的自包含 HTML 报告——另有 ffmpeg 录屏、Android/iOS 设备录屏、Windows 单窗口录制与系统声音采集。
---

# 报告与录屏

## HTML 运行报告

默认情况下,每次运行在 bot 关闭时写出一份自包含 HTML 报告,带逐步截图。
出错或 Ctrl+C 也会生成,方便看到停在哪一步。报告完全由运行期间采集的
数据构建,不调用模型、不联网。

```python
bot = Qirabot(task_name="checkout")            # 默认:./qira_runs/<日期>/<时间-id>/
bot = Qirabot(report_dir="./artifacts")        # 自定义根目录(或 QIRA_REPORT_DIR)
bot = Qirabot(report=False)                    # 完全关闭——CI / 库场景
```

每次运行的输出结构:

```
qira_runs/2026-06-07/192335-3f9ab2c1/
  report.html          # 自包含:内嵌缩略图 + 每个 ai() 任务的结果徽章
  screenshots/         # 全分辨率帧(001_click.jpg、002_type_text.jpg…)
  recording.mp4        # 若开启录制——同时嵌入报告
```

### 报告内容

**页头。** 标题优先用 `task_name`,没设置时取唯一任务指令的第一行,多个
任务时写「N tasks」。指令较长或有多行时最多显示两行,完整内容点「Full
instruction」展开。标题下面依次是运行时间、平台和屏幕尺寸、Run ID,再往下
是三栏面板:

- **Outcome**:所有 `ai()` 任务通过为绿色,有任务失败为红色,只有截断
  (`MAX STEPS` / `MAX DURATION`)或取消时为琥珀色;另有通过数和步数。
- **Time**:AI 步骤的总耗时,拆成模型耗时、设备耗时(截图、动作、等待
  稳定、自定义工具)和因容量损失的时间(限流、故障、超时),数据与
  `bot.usage` 一致。
- **Model & tokens**:模型(及兜底模型)、档位(及升档情况)和 token 总数。
  点击总数弹出「模型 × 档位」明细表:AI calls、Prompt、Cached、Response、
  Thinking、Total。失败的调用也计入;没有应答告知档位时,档位显示 `—`。
  打印时明细表自动展开。

**任务概览。** 有两个及以上 `ai()` 任务时,每个任务一行:状态、指令、步数、
耗时和时间构成。点击可跳到对应任务。

**步骤。** 每个任务一张表(单任务时标题为「Steps」,多任务时为「Task N」),
手动步骤按实际执行位置分组。每个 `ai()` 任务的徽章对应 `result.status`,
失败原因显示在表格上方。列为 `# / Time / Action / Detail / Model /
Duration / Waits / Screenshot`。本次运行的主模型以灰色显示,调用以其他档位
服务时带档位标签。Duration 下面有模型 / 设备 / 等待的时间条;因容量等待过的
步骤会标出各项等待并整行高亮。手动(非 AI)步骤这三列显示 `—`。

`screenshot_annotate=True`(默认)在点击/输入坐标处画红色十字线。点击缩略图
可翻看全部截图;有录像时,点时间列可跳到视频对应位置。
`bot.report("path.html")` 可按需额外输出一份;`bot.screenshot(target)`
抓单帧。

## 宿主机录屏

```python
bot = Qirabot(record=True)          # 或 QIRA_RECORD=1
page = bot.open("https://example.com")
bot.ai(page, "执行任务")
bot.close()                         # 停止录制,然后写报告
```

也可手动控制:

```python
bot.start_recording()               # 幂等;帧率用 record_fps
try:
    bot.ai(page, "执行任务")
finally:
    bot.stop_recording()            # 每次运行一份录像——重新开始会覆盖
```

需要 PATH 里有 `ffmpeg`。macOS:给终端/IDE 授予"屏幕录制"权限(否则录出
黑屏),多显示器用 `QIRA_SCREEN_INDEX` 选择。录制是尽力而为:缺 ffmpeg 或
权限被拒只警告,不会导致任务失败(排查看运行目录里的
`recording.ffmpeg.log`)。

## 设备录屏(Android / iOS)

默认录的是宿主机屏幕,手机画面不在其中。两个开关改录设备自己的屏幕
(CLI 的 `android` / `ios --record` 用的就是它们):

```python
# Android(或任何 Appium driver):录制器根据动作目标解析
bot = Qirabot(record_device=True)   # AdbDevice -> adb screenrecord;Appium -> 会话 API
bot.ai(dev, "打开设置")
bot.close()                         # 视频拉取到 report_dir/recording.mp4

# iOS 走 WDA(无 Appium):录 WDA 的 MJPEG 流(端口 9100;
# USB 真机:`iproxy 9100 9100`)。宿主机需要 ffmpeg。
bot = Qirabot(record_mjpeg_url="http://127.0.0.1:9100")
```

`adb screenrecord` 超过 3 分钟上限的分段会用 ffmpeg 合并。如果你自己
`driver.quit()` Appium,请先调 `bot.stop_recording()`,因为视频存在会话里。

CLI 的 `--record` 参数按目标映射到这些开关,见
[CLI 参考](/zh/guide/cli)。每个 `record*` 构造参数及其环境变量的完整清单
在[配置](/zh/advanced/configuration)。

## Windows:单窗口录制 + 系统声音

```python
from qirabot import Qirabot, Window

window = Window(title_re="Notepad.*")
bot = Qirabot(record=True, record_window=True, record_audio=True)
bot.ai(window, "输入一条笔记")
bot.close()                         # recording.mp4 = 只有该窗口,带声音
```

- `record_window=True` 只录被测窗口(Windows 窗口后端;其他情况回退全
  屏),做法是抓桌面再裁剪到窗口矩形,因此游戏等 GPU 合成窗口也能正常
  录上。请保持窗口可见且不要移动:盖在该矩形上的内容会一起录进去,
  最小化则录不到画面。`gdigrab` 备选模式见
  [Windows 与游戏](/zh/backends/windows-games#录制窗口)。
- `record_audio=True` 通过 DirectShow 环回设备采集系统声音:安装
  [screen-capture-recorder](https://github.com/rdp/screen-capture-recorder-to-video-windows-free)
  或启用"立体声混音"。自动检测;可用设备名或 `QIRA_AUDIO_DEVICE` 指定;
  音画不同步用 `record_audio_offset=-0.4` 微调。
