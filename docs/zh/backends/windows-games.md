---
title: 用 AI 自动化 Windows 应用与游戏——DirectInput 扫描码
description: 按标题或 HWND 绑定单个 Windows 窗口,用 AI 视觉驱动。输入为 Unity、Unreal 和原生游戏真正读取的 DirectInput 扫描码——虚拟键自动化无法触达的层级。
---

# Windows 与游戏——Window 后端

`qirabot.Window` 绑定单个窗口(按标题或 HWND):截图取其客户区,
点击按窗口相对坐标,按键发送的是 **DirectInput 扫描码**。扫描码是游戏
真正轮询的层级,虚拟键自动化(pyautogui、AutoHotkey 默认发送模式)到
不了这里。实现只用标准库 ctypes,内置在核心包里,不需要 extras。

配合 AI 视觉定位,它可以驱动基于 DOM 或无障碍树的框架处理不了的目标:
Unity 和 Unreal 游戏、自定义启动器、遗留原生应用。

最快的验证方式是 CLI(内置能力,不需要额外 extras):

```bash
qirabot desktop "打开背包并列出所有物品" --window-title "Genshin"
qirabot desktop "..." --hwnd 132456
```

`--window-title` 走的是正则(对应下面的 `title_re=` 选择器),直接粘贴
任务栏标题时记得转义括号和点号。想用 `--hwnd` 又不知道句柄:可以用
Spy++,也可以故意写一个宽泛的 `--window-title`——"多个窗口同时匹配"的
报错会把每个候选按 `'标题' (hwnd=...)` 列出来。

同样的事在 Python 里:

```python
from qirabot import Qirabot, Window

window = Window(title="Genshin")   # 标题子串匹配;或 Window(hwnd=132456)
bot = Qirabot().bind(window)

result = bot.ai("打开背包并列出所有物品")
bot.close()
```

`Window` 的选择器:`hwnd=`(显式句柄)、`title=`(字面子串,直接粘贴
任务栏里的标题即可,括号、点号等都按字面匹配)、`title_re=`(正则,用于
模糊/多语言匹配)、`class_name=`(精确窗口类名,Unity 游戏是
`UnityWndClass`、Unreal 是 `UnrealWindow`;比标题更稳定,可与
`title`/`title_re` 组合缩小范围)。多个窗口同时匹配时默认报错并列出候选;
如果重名不可避免(云游戏客户端、启动器悬浮窗常和主窗口标题完全相同),
就加 `ambiguous="largest"`(CLI:`--ambiguous largest`)自动选面积最大的
窗口。运行 qirabot 的控制台窗口永远不会成为候选:它的标题会回显完整命令行
(连同你输入的匹配模式),否则会匹配到自己。`timeout=` 会在窗口尚未
出现时持续轮询,适合刚启动还在加载的游戏:

```python
window = Window(title="MyGame · Cloud(Beta)", ambiguous="largest")
window = Window(class_name="UnityWndClass", timeout=180)   # 刚启动的游戏
```

绑定窗口换来的是窗口相对坐标和客户区截图,不是"后台静默运行":输入走
`SendInput`,跟的是焦点而不是坐标,所以每次点击和按键前后端都会把目标
窗口提到前台(最小化的先还原)。截图这一侧宽松些——`PrintWindow` 能拍到
被部分遮挡的窗口,只有 GPU 合成(游戏)窗口才回退到屏幕抓取,那种情况
要求窗口可见。所以请按"这台机器在跑任务时归它用"来规划,通常做法是用
一台备用机或虚拟机。

每次打字/按键前,后端会把持有焦点控件的输入语言切到英文并关闭 IME:中文
输入法开着时,注入的字母键会被输入法候选窗截走,游戏收不到。IME 状态挂在
焦点控件的输入上下文上,文本框一获得焦点就会带回来,所以每次调用都会重新
切换并读回验证。窗口拒绝切换时,文本改走剪贴板粘贴,粘贴完全绕过
输入法组合。注入中文永远不需要中文输入法(非 ASCII 文本本来就走粘贴路径),
所以强制英文没有任何损失。只影响目标窗口(Win+Space 可切回);传
`Window(..., english_ime=False)` 可关闭此行为。

## 游戏级输入

下面的片段沿用上面 bind 过的 `bot`,窗口是隐含的;如果用未绑定的
`Qirabot()`,每个调用都要把窗口作为第一个参数传进去
(`bot.press_key(window, "w", ...)`)。

- **按键是扫描码**:真正的硬件级输入,包括 `ctrl`/`alt`/`win` 组合键。
  扫描码表之外的字符以 unicode 键事件注入。
- **按住指定时长**,用于定量的游戏内移动:

  ```python
  bot.press_key("w", duration_seconds=2)          # 前进 2 秒
  bot.press_key("shift+w", duration_seconds=1.5)  # 疾跑
  ```

- **修饰键点击**:原子化的 alt+点击(游戏)、ctrl+点击多选:

  ```python
  bot.click("敌方单位", modifier="alt")
  ```

- **按下/释放拆分原语**:`mouse_down` / `mouse_up` / `key_down` /
  `key_up` 可以在执行其他动作时保持某个输入按住(边移动边点击、按住
  拖拽)。`ai()` 运行结束和 `close()` 时会自动释放仍按住的输入。

## 确定性步骤与 AI 混用

游戏 UI 巡检适合"确定性导航 + AI 验证"的组合:

```python
bot.click("背包图标")
bot.wait_for("背包面板已打开")
ok = bot.verify("每个物品格都显示图标和数量")
items = bot.extract("列出背包中可见的物品名称")
```

完整演练见
[examples/game/](https://github.com/qirabot/qirabot/tree/main/examples/game),
其中包含自定义工具示例:AI 在任务中途调用你的 GM 后端(体力不足弹窗时
加体力,然后继续日常任务循环)。如何注册这类工具见
[AI 任务与自定义工具](/zh/advanced/ai-tasks)。

## 录制窗口

Windows 上可以只录被测窗口,并采集系统声音:

```python
bot = Qirabot(record=True, record_window=True, record_audio=True)
```

录制方式是抓桌面再按窗口矩形裁剪,拿到的是合成后的画面,所以 DirectX
和全屏游戏都能正常录上。代价是裁剪按位置固定:录制期间保持窗口可见且
不要移动,否则盖在那块矩形上的东西会一起进视频,最小化则录不到有效画面。

设 `QIRA_RECORD_WINDOW_NATIVE=1` 可切回旧的 `gdigrab` 逐窗口模式:它能
跟随被遮挡或在后台的窗口,窗口移动也不受影响,但对 GPU 合成(游戏)
窗口会录出黑帧——这个模式适合原生应用,不适合游戏。

## 说明

- 全桌面自动化(任意系统)是独立的 [pyautogui 后端](/zh/backends/desktop);
  Window 后端专为 Windows 单窗口设计。
- 如果从 Airtest 1.x 迁移,`connect_device("Windows:///132456")` 改为
  `Window(hwnd=132456)`。
