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

每个 `ai()` 任务的徽章对应 `result.status`:绿色 `PASS`、红色
`FAIL`/`ERROR`、琥珀色 `MAX STEPS`(步数截断)。
`screenshot_annotate=True`(默认)在点击/输入坐标处画红色十字线。
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
