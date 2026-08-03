---
title: 进度悬浮窗与 ESC 急停
description: 置顶进度小窗、标记真实键鼠接管的琥珀色边缘光晕、长按 ESC 急停——截图排除、点击穿透,任何平台上都可以放心常开。
---

# 进度悬浮窗与急停

CLI 的每个任务命令都会在屏幕右下角显示一个置顶小窗:当前指令、每一步的
动作和模型思考、以及最终的 ✓/✗ 结果。用 `--no-overlay` 关闭;SDK 中是
`Qirabot(overlay=True)`(默认关闭)。

窗口的设计保证它永远不会干扰任务本身:

- **截图排除**——macOS `NSWindowSharingNone`、Windows
  `WDA_EXCLUDEFROMCAPTURE`——不会出现在 bot 自己的截图里,不会干扰模型。
- **点击穿透**,不会挡住要点给下层应用的点击。
- 一切失败都静默降级:没有 GUI 的环境(Linux、CI、无显示器)悬浮窗
  就是个 no-op。

## 边缘光晕:"请勿操作"信号

当任务驱动的是**真实键鼠**(桌面后端:`Window`、pyautogui)时,任务期间
屏幕四边会亮起缓慢呼吸的琥珀色光晕——与屏幕共享边框同一套视觉语言:
*机器正被接管,请勿操作*。远程协议目标(浏览器、Android、iOS)不会点亮
它:那些场景下你的键鼠依然是你的。

光晕与窗口一样被截图排除,且规则更严格:在不支持排除的 Windows 版本上
它干脆不显示——四边发光的截图会干扰模型识别。

不只是 `bot.ai()`:桌面后端上的单步调用(`bot.click`、`bot.press_key`、
`bot.type_text` 等)同样注入真实输入,所以也会点亮光晕——第一次调用时
亮起,最后一次调用后几秒淡出,连续的脚本化操作显示为一段完整的接管,
而不是闪烁。

## 长按 ESC 中止

光晕亮着时,屏幕顶部居中会显示一个小提示丸("Hold ESC to stop · 长按
ESC 中止")。**长按 ESC 约一秒**即可中止任务:

- bot 会在下一个步骤边界停下。一步可能需要几秒——在途的模型调用要先
  返回——但期间不会再注入任何输入。
- bot 按住的所有按键和鼠标键全部松开。
- `bot.ai()` 抛出 `user_abort` 错误,任务被记录为**已取消
  (cancelled)**而非失败——主动中止不会污染你的失败率统计。

短促的 ESC 点击——无论是你按的还是 bot 注入的——都不会触发。

中止具**粘性**:同一 bot 实例后续所有 `bot.ai()` 都会立即抛出
`user_abort`(不点光晕、不碰键鼠),脚本里的 try/except 无法把你刚收回
的机器再抢回去——要继续必须显式调用 `bot.clear_user_abort()`。单步调用
不受影响,可用于善后清理。

急停开关随悬浮窗生效:`--no-overlay`(或 `overlay=False`)会连同它一起
关闭。pyautogui 后端下还有一个完全不依赖悬浮窗的兜底:把鼠标甩到屏幕
任意角落并停住,也会中止任务(pyautogui 内置 failsafe)。

macOS 上 ESC 监听需要辅助功能权限——与桌面控制本身需要的是同一个。
没有该权限时监听静默降级,角落 failsafe 依然有效。

## SDK:全自动窗口

常规用法一个参数——窗口由 bot 全自动驱动:标题行是指令 + 运行状态点 +
耗时,正文是 `step 3/20 · click · "…"` 加模型思考,结束变 ✓/✗:

```python
bot = Qirabot(overlay=True)   # 每次 bot.ai() 的进度都会显示到窗口
```

## SDK:standalone `Overlay`

当你的脚本不只有 `bot.ai()` 一件事时,可以自己持有窗口:standalone 的
`Overlay` 想显示什么、什么时候显示都由你决定——自己的阶段也能上窗——
AI 那一段则用 `ov.step` 喂入 bot 步骤:

```python
from qirabot import Overlay

with Overlay() as ov:
    ov.begin("阶段 1/3:下载数据…")
    data = download_from_api()                    # 与 bot 无关的自有代码

    ov.begin("阶段 2/3:AI 填写报表系统…",
             edge_glow=True)                      # 接下来接管真实键鼠
    bot.ai(pyautogui, "把数据导入报表系统",
           on_step=ov.step)                       # bot 步骤显示到窗口

    ov.begin("阶段 3/3:发送汇总邮件…")
    send_email(data)
```

已经有自己的 `on_step` 回调?`on_step=ov.wrap(my_cb)` 两个都会调。

可运行示例:
[overlay_progress.py](https://github.com/qirabot/qirabot/blob/main/examples/desktop/overlay_progress.py)。

## 平台说明与已知限制

- **macOS**:支持随 qirabot 自动安装(pyobjc)。
- **Windows**:用标准库 tkinter。完整的截图排除需要 Windows 10 2004+,
  更早的版本截图中显示黑块而非窗口内容(边缘光晕按上文更严格的规则,
  在这些版本上干脆不显示)。
- **其余环境**(Linux、CI、无 GUI):悬浮窗静默不生效,绝不会影响任务
  本身。
- **独占全屏游戏**绕过桌面合成器,任何置顶窗口——悬浮窗和光晕——都
  显示不出来。任务本身不受影响;想看到悬浮窗请用无边框/窗口化模式。
- ESC 中止后到完全停下的残余延迟,是在途模型调用在收尾——期间不会再
  注入任何输入。
