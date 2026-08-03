---
title: 用 AI 自动化 Windows 应用与游戏——DirectInput 扫描码
description: 按标题或 HWND 绑定单个 Windows 窗口,用 AI 视觉驱动。输入为 Unity、Unreal 和原生游戏真正读取的 DirectInput 扫描码——虚拟键自动化无法触达的层级。
---

# Windows 与游戏——Window 后端

`qirabot.Window` 绑定**单个窗口**(标题正则或 HWND):截图取其客户区,
点击按窗口相对坐标,按键是 **DirectInput 扫描码**——游戏真正轮询的层级,
虚拟键自动化(pyautogui、AutoHotkey 默认发送模式)无法触达。仅用标准库
ctypes;内置在核心包,无需 extras。

配合 AI 视觉定位,它能驱动任何基于 DOM/无障碍树的框架都无能为力的目标:
Unity 和 Unreal 游戏、自定义启动器、遗留原生应用。

最快的验证方式是 CLI——内置能力,无需 extras:

```bash
qirabot desktop "打开背包并列出所有物品" --window-title "Genshin"
qirabot desktop "..." --hwnd 132456
```

同样的事在 Python 里:

```python
from qirabot import Qirabot, Window

window = Window(title="Genshin")   # 标题子串匹配;或 Window(hwnd=132456)
bot = Qirabot().bind(window)

result = bot.ai("打开背包并列出所有物品")
bot.close()
```

`Window` 的选择器:`hwnd=`(显式句柄)、`title=`(字面子串——直接粘贴
任务栏里的标题即可,括号、点号等都按字面匹配)、`title_re=`(正则,用于
模糊/多语言匹配)、`class_name=`(精确窗口类名——Unity 游戏是
`UnityWndClass`、Unreal 是 `UnrealWindow`;比标题更稳定,可与
`title`/`title_re` 组合缩小范围)。多个窗口同时匹配时默认报错并列出候选;
如果重名不可避免——云游戏客户端、启动器悬浮窗常和主窗口标题完全相同——
加 `ambiguous="largest"`(CLI:`--ambiguous largest`)自动选面积最大的
窗口。运行 qirabot 的控制台窗口永远不会成为候选:它的标题会回显完整命令行
(连同你输入的匹配模式),否则会匹配到自己。`timeout=` 会在窗口尚未
出现时持续轮询,适合刚启动还在加载的游戏:

```python
window = Window(title="MyGame · Cloud(Beta)", ambiguous="largest")
window = Window(class_name="UnityWndClass", timeout=180)   # 刚启动的游戏
```

每次打字/按键前,后端会把持有焦点控件的输入语言切到英文并关闭 IME——中文
输入法开着时,注入的字母键会被输入法候选窗截走,游戏收不到。IME 状态挂在
焦点控件的输入上下文上,文本框一获得焦点就会带回来,所以每次调用都会重新
切换并**读回验证**:窗口拒绝切换时,文本改走剪贴板粘贴——粘贴完全绕过
输入法组合。注入中文永远不需要中文输入法(非 ASCII 文本本来就走粘贴路径),
所以强制英文没有任何损失。只影响目标窗口(Win+Space 可切回);传
`Window(..., english_ime=False)` 可关闭此行为。

## 游戏级输入

- **按键是扫描码** —— 真正的硬件级输入,包括 `ctrl`/`alt`/`win` 组合键。
  扫描码表之外的字符以 unicode 键事件注入。
- **按住指定时长** —— 定量的游戏内移动:

  ```python
  bot.press_key(window, "w", duration_seconds=2)          # 前进 2 秒
  bot.press_key(window, "shift+w", duration_seconds=1.5)  # 疾跑
  ```

- **修饰键点击** —— 原子化的 alt+点击(游戏)、ctrl+点击多选:

  ```python
  bot.click(window, "敌方单位", modifier="alt")
  ```

- **按下/释放拆分原语** —— `mouse_down` / `mouse_up` / `key_down` /
  `key_up` 可以在执行其他动作时保持某个输入按住(边移动边点击、按住
  拖拽)。`ai()` 运行结束和 `close()` 时会自动释放仍按住的输入。

## 确定性步骤与 AI 混用

游戏 UI 巡检适合"确定性导航 + AI 验证"的组合:

```python
bot.click(window, "背包图标")
bot.wait_for(window, "背包面板已打开")
ok = bot.verify(window, "每个物品格都显示图标和数量")
items = bot.extract(window, "列出背包中可见的物品名称")
```

完整演练见
[examples/game/](https://github.com/qirabot/qirabot/tree/main/examples/game),
其中包含自定义工具示例:AI 在任务中途调用你的 GM 后端(体力不足弹窗时
加体力,然后继续日常任务循环)——如何注册这类工具见
[AI 任务与自定义工具](/zh/advanced/ai-tasks)。

## 录制窗口

Windows 上可以只录被测窗口,并采集系统声音:

```python
bot = Qirabot(record=True, record_window=True, record_audio=True)
```

保持窗口可见——`gdigrab` 对最小化或 GPU 合成(独占全屏游戏)的
窗口会录出黑帧;这类场景请改录全屏。

## 说明

- 全桌面自动化(任意系统)是独立的 [pyautogui 后端](/zh/backends/desktop);
  Window 后端专为 Windows 单窗口设计。
- 从 Airtest 1.x 迁移?`connect_device("Windows:///132456")` 改为
  `Window(hwnd=132456)`。
