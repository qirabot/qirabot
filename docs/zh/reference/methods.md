---
title: 方法参考——签名、参数与返回值
description: Qirabot 每个方法的完整签名——click、type_text、extract、verify、locate、wait_for、ai、open、bind、press_key、scroll、录屏与生命周期调用,以及带 token 用量字段的结果对象。
---

# 方法参考

`Qirabot` 的每个公开方法及其完整签名。构造参数见
[配置](/zh/advanced/configuration);各底层动作在每个平台的行为见
[平台支持矩阵](/zh/reference/api#平台支持矩阵)。

先说明两点:

- **`target`** 永远是第一个参数:`bot.open()` 返回的 page、你自己的
  Playwright `page` / Selenium / Appium `driver`、
  `AdbDevice` / `WdaClient` / `Window`,或 `pyautogui` 模块。在
  [bind 绑定的 bot](/zh/backends/custom-adapters#bind-——-省去重复的第一个参数)
  上,它从所有调用中消失。
- `right_click`、`hover`、`clear_text`、`drag` 之类的动作出现在平台矩阵
  里,但**不是**直接的 `bot.*` 方法——它们是模型在 [`ai()`](#ai) 运行中
  使用的工具。

## 通用参数

AI 定位动作和 AI 操作共享这些关键字参数——在此统一说明:

| 参数 | 默认值 | 含义 |
|---|---|---|
| `timeout` | `0.0` | 自动等待:轮询到元素出现(最长这么多秒)再执行动作;`0` 立即执行。超时抛 `QirabotTimeoutError`。 |
| `interval` | `2.0` | 自动等待的轮询间隔(秒)。 |
| `wait` | `""` | 覆盖 `timeout` 使用的自动推导的存在性断言。 |
| `retry` | 构造函数的 `retry` | 按调用覆盖瞬时失败的重试次数。 |
| `thinking_level` | 构造函数的 | 按调用覆盖[思考深度](/zh/advanced/configuration#思考深度)：`minimal` / `low` / `medium` / `high`；留空 = 使用引擎默认值。 |
| `language` | 构造函数的 | 按调用覆盖响应语言。 |

## 会话与生命周期

### bind()

```python
bind(target) -> bound bot
```

一次固定目标;之后下面的每个方法都省去第一个参数。
`with Qirabot().bind(driver) as bot:` 同样可用。见
[自定义 Adapter 与挂载](/zh/backends/custom-adapters)。

### open()

```python
open(url="", headless=False, *, viewport=(1280, 800), user_data_dir="",
     channel="", args=None, cdp_url="") -> page
```

启动 Chromium(需要 `qirabot[browser]`)并返回 Playwright page。
`channel` 使用已安装的浏览器(`"chrome"`、`"msedge"`);`user_data_dir`
保持持久化的用户配置;`args` 是额外的 Chromium 启动参数列表;`cdp_url`
附加到已运行的 Chrome 而不是新启动(与各启动选项互斥)。无显示器的机器
上自动降级为 headless 并给出警告。见[浏览器](/zh/backends/browser)。

### current_page()

```python
current_page(target) -> page
```

当前活动的页面/目标——点击打开新标签页后,可能与最初传入的不同。主要
用于 bind 绑定的 bot,因为你看不到返回的 page。

### close()

```python
close() -> None
```

释放仍按住的输入、停止录屏、写出 [HTML 报告](/zh/advanced/reports)、
关闭 `open()` 启动的资源,并把运行记录为完成。`atexit` 和上下文
管理器退出时自动调用。绝不关闭你自己创建的浏览器/driver。

### fail() / cancel()

```python
fail(error_message="") -> None
cancel(reason="") -> None
```

记录 `close()` 默认记录的“成功完成”之外的终态:`fail()` 把运行标记为
失败,`cancel()` 标记为主动中止——都会体现在 HTML 报告里。在 `close()`
之前调用。

### report_dir / task_id

属性:每次运行的输出目录(`./qira_runs/<date>/<time-id>/`)和本地运行
id(形如 `local-<8 位十六进制>`)。

## AI 定位动作

全部返回**当前目标**——浏览器上点击可能打开新标签页,记得重新赋值
(`page = bot.click(page, ...)`)。全部接受[通用参数](#通用参数)。

### click()

```python
click(target, locate, *, modifier="", timeout=0.0, interval=2.0, wait="",
      retry=None, thinking_level="", language="") -> target
```

`locate` 是自然语言的元素描述(任何语言均可)。`modifier` 在点击前后
按住修饰键——`"alt"`、`"ctrl+shift"`——仅桌面后端。

### double_click()

```python
double_click(target, locate, *, <common>) -> target
```

触屏平台用两次快速点按。

### type_text()

```python
type_text(target, locate, text, *, press_enter=False,
          clear_before_typing=False, <common>) -> target
```

定位输入框、聚焦、输入 `text`(中文/emoji 均可)。**空 `locate` 跳过
AI 定位**,输入到当前拥有键盘焦点的元素——无 AI、无模型调用;该模式下
`timeout`/`wait` 被忽略。

### long_press()

```python
long_press(target, locate, *, duration=2.0, <common>) -> target
```

仅触屏平台(Android/iOS)——浏览器/桌面抛 `NotImplementedError`。

### mouse_down() / mouse_up()

```python
mouse_down(target, locate, *, <common>) -> target
mouse_up(target, locate="", *, <common>) -> target
```

拆分的按下/释放,用于按住拖动——仅桌面后端。`mouse_up` 不传 `locate`
时在当前光标位置释放(无 AI、无模型调用)。`ai()` 运行结束和 `close()`
时自动释放仍按住的输入。

### key_down() / key_up()

```python
key_down(target, key) -> target
key_up(target, key) -> target
```

在执行其他动作期间按住某个键(仅桌面后端)。无 AI、无模型调用。

## AI 操作

### ai()

```python
ai(target, instruction, max_steps=20, *, on_step=None, thinking_level="",
   language="", custom_tools=None, exclude_tools=None, knowledge=None) -> RunResult
```

自主循环:截图 → 决策 → 执行,直到完成或达到 `max_steps`。每步之后以
[`StepResult`](#stepresult) 为参数调用 `on_step`。`custom_tools` 把你的
Python 函数注册为可调用工具;`exclude_tools` 按动作名移除内置工具——
两者详见 [AI 任务与自定义工具](/zh/advanced/ai-tasks)。`knowledge` 为本次
运行挂载领域参考材料(文本、`Path` 或二者的列表;合计 32KB)。

### extract()

```python
extract(target, instruction, *, retry=None, thinking_level="",
        language="") -> ExtractResult
```

直接从屏幕提取结构化数据。返回值
[是 `str` 的子类](#extractresult),携带 token 用量。

### verify()

```python
verify(target, assertion, *, retry=None, thinking_level="",
       language="") -> VerifyResult
```

视觉断言。断言不成立不抛异常——结果
[按真假值使用](#verifyresult),带 `.reason`;传输/模型端点错误仍会抛出。

### locate()

```python
locate(target, locate, *, timeout=0.0, interval=2.0, wait="",
       retry=None, thinking_level="", language="") -> LocateResult
```

把自然语言元素描述解析成坐标,**不执行任何动作**——不点击、不输入。
返回 [`LocateResult`](#locateresult),支持元组解包:

```python
x, y = bot.locate(page, "确定按钮")
page.mouse.click(x, y)   # 拿坐标驱动你自己的框架调用
```

坐标位于 **adapter 的截图像素坐标系**:Windows 窗口后端是窗口相对的客户
区像素,pyautogui 是物理屏幕像素,移动端是设备像素——与 bot 自身动作
使用的坐标系一致,也就是报告截图里看到的位置,但不一定是操作系统全局
坐标。

locate 本身仅一次 vision 调用(无 LLM token)。`timeout > 0` 时会先自动
等待,语义与 `click()` 相同——每次轮询是发往你模型端点的一次 LLM verify
调用。

::: warning 元素不存在时
元素**不在屏幕上**时视觉解析器仍会返回坐标,且该坐标不可信。无法保证
元素存在时,请传 `timeout=` 或先用 `verify()` / `wait_for()` 确认。
:::

### wait_for()

```python
wait_for(target, assertion, timeout=30.0, interval=2.0, *,
         thinking_level="", language="") -> None
```

按 `verify` 语义每 `interval` 秒轮询一次;条件一成立立即返回,`timeout`
到期抛 `QirabotTimeoutError`。每次轮询都是发往你模型端点的一次 verify
调用——为了正确性优先用它取代 sleep,同时把 `interval` 设得合理以控制
token 用量。

## 直接动作——无 AI

### navigate() / go_back() / close_tab()

```python
navigate(target, url) -> target      # 缺协议时自动补 "https://"
go_back(target) -> target            # Playwright 上智能:关闭没有历史的新标签页
close_tab(target) -> target          # 仅 Playwright
```

各平台可用性见[矩阵](/zh/reference/api#平台支持矩阵);智能 `go_back`
的行为见 [API 参考](/zh/reference/api#导航、滚动与按键-无-ai)。

### scroll()

```python
scroll(target, direction="down", distance=3, *, x=None, y=None) -> None
```

在视口中心滚动,给定 `(x, y)` 时在该点滚动。

### press_key()

```python
press_key(target, key, duration_seconds=0) -> target
```

一个键名全平台通用——Android 上是 adb keycode,Windows 窗口后端是
DirectInput 扫描码。组合键用 `+` 连接(`"ctrl+shift+t"`,仅桌面/浏览器)。
`duration_seconds > 0` 按住指定时长再释放(上限 10 秒;仅 pyautogui +
Windows 窗口后端)。按键词汇表:
[API 参考](/zh/reference/api#导航、滚动与按键-无-ai)。

### screenshot()

```python
screenshot(target) -> Path | None
```

保存到 `report_dir/screenshots/`,返回保存路径(`report=False` 时返回
`None`)。

### launch_app()

```python
launch_app(app, *, wait=2.0) -> None
```

启动或激活桌面应用,然后等 `wait` 秒等待其窗口出现。也可独立导入:
`from qirabot import launch_app`。各操作系统的机制:
[API 参考](/zh/reference/api#启动桌面应用-无-ai)。

## 报告与录屏

```python
report(path=None) -> Path | None     # 立即写出 HTML 报告(close 时自动)
start_recording(*, fps=None, target=None, window=None, audio=None) -> bool
stop_recording() -> str | None       # 返回保存路径
```

通常不需要手动调用——构造函数上的 `record=True` / `record_device=True` /
`record_mjpeg_url=...` 负责录屏,`close()` 写出报告。手动控制和全部开关:
[报告与录屏](/zh/advanced/reports)。

## 结果对象

### RunResult

`ai()` 的返回值。

| 字段 | 类型 | 含义 |
|---|---|---|
| `success` | `bool` | 当且仅当 `status == "completed"` 时为 `True` |
| `status` | `str` | `"completed"` / `"goal_failed"` / `"max_steps"` / `"error"`——见[错误处理](/zh/advanced/error-handling) |
| `output` | `str` | 模型的最终回答/总结 |
| `steps` | `list[StepResult]` | 执行过的每一步 |

### StepResult

`ai()` 每一步一条;也是 `on_step` 收到的参数。

| 字段 | 类型 | 含义 |
|---|---|---|
| `step` | `int` | 从 1 开始的步骤序号 |
| `action_type` | `str` | 执行的动作(`click`、`scroll`、自定义工具名等) |
| `params` | `dict` | 动作参数 |
| `output` | `str` | 反馈给模型的动作结果 |
| `finished` | `bool` | 最后一步为 `True` |
| `decision` | `str` | 模型此步的推理 |
| `input_tokens` / `output_tokens` / `thinking_tokens` | `int` | 此步的 token 用量 |
| `step_duration_ms` / `llm_decision_duration_ms` | `int` | 实际耗时 |

### ExtractResult

`extract()` 的返回值——`str` 的子类,可直接当提取文本使用。额外字段:
`input_tokens`、`output_tokens`、`thinking_tokens`。`output_tokens` 已经
包含 `thinking_tokens`,因此一次调用的花费是 `input_tokens +
output_tokens`。注意:会生成新字符串的 str 操作(切片、`.strip()`、
拼接)返回普通 `str`,token 字段随之丢失——请在 `extract()` 返回的原值
上读取。

### VerifyResult

`verify()` 的返回值——断言成立时为 truthy,可直接放进 `assert` / `if`。
字段:`passed`(`bool`)、`reason`(模型的解释——断言意外失败时值得记录
日志),以及与 `ExtractResult` 相同的三个 token 字段。

### LocateResult

[`locate()`](#locate) 的返回值,支持元组解包:`x, y = bot.locate(...)`。

| 字段 | 类型 | 含义 |
|---|---|---|
| `x` / `y` | `int` | 解析出的坐标,位于 adapter 的截图像素坐标系 |
| `input_tokens` / `output_tokens` / `thinking_tokens` | `int` | LLM token 用量——当前恒为 `0`(locate 仅是一次 vision 调用) |
