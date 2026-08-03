---
title: API 参考——动作与平台支持
description: Qirabot 全部动作——AI 定位的点击与输入、extract/verify/wait_for、bot.ai()、不经模型调用的直接导航与按键、完整的平台支持矩阵,以及运行生命周期。
---

# API 参考

本页是工作总览——每个调用做什么、在各平台如何映射。完整签名、每个
关键字参数以及结果对象见[方法参考](/zh/reference/methods)。

## 简单动作(AI 定位)

轻量的视觉元素定位——每次调用是对你配置的模型的一次请求:

```python
# 按描述点击元素
bot.click(page, "登录按钮")

# 自动等待:轮询到元素出现(最长 timeout)再点击,否则抛
# QirabotTimeoutError。所有框架通用。`wait` 可覆盖自动推导的断言。
# (type_text/double_click 同样支持。)
bot.click(page, "登录按钮", timeout=15.0, interval=2.0)

# 修饰键点击:点击时按住修饰键(仅桌面)
bot.click(target, "敌方单位", modifier="alt")         # alt+点击(游戏)
bot.click(target, "文件行", modifier="ctrl+shift")     # 多个用 "+" 连接

# 向输入框输入文本
bot.type_text(page, "邮箱输入框", "user@example.com")

# 从屏幕提取数据
text = bot.extract(page, "提取主标题")

# 视觉断言——断言不成立不抛异常,而是返回 falsy 的
# VerifyResult(带 .reason);断言成立时为 truthy
ok = bot.verify(page, "成功提示可见")

# 等待条件成立(卡点):成立即返回,否则抛 QirabotTimeoutError。
# 需要不抛异常的布尔检查用 verify()。
bot.wait_for(page, "页面已加载完成", timeout=15.0, interval=2.0)
```

`click`、`type_text`、`double_click` 返回当前目标(与传入类型一致)。动作
在**新标签页**打开链接时,返回值就是那个新标签页——重新赋值以继续操作
活动页面:

```python
page = bot.click(page, "打开第一个视频")  # 可能切到新标签页
```

## 多步 AI —— bot.ai()

```python
result = bot.ai(page, "搜索 SpaceX 并总结第一条结果", max_steps=10)
print(result.success, result.status, result.output)
```

完整说明——步骤回调、`custom_tools`、`exclude_tools`——见
[AI 任务与自定义工具](/zh/advanced/ai-tasks);运行结果见
[错误处理](/zh/advanced/error-handling)。

## 导航、滚动与按键(无 AI)

不需要 AI 元素定位的直接动作。`go_back`、`navigate`、`close_tab`、
`press_key` 返回当前页面/目标(动作后可能变化);`scroll` 返回 `None`。

```python
bot.navigate(page, "example.com")   # 协议可省略;自动补 "https://"
bot.go_back(page)                   # 返回上一页(智能,见下)
page = bot.close_tab(page)          # 关闭当前标签页,回到上一个
bot.scroll(page, "down", 3)         # 在视口中心滚动
bot.scroll(page, "up", distance=5, x=640, y=400)  # 在指定点滚动
bot.press_key(page, "Enter")        # 单个按键
bot.press_key(page, "ctrl+c")       # 组合键(用 "+" 连接)
bot.press_key(target, "w", duration_seconds=2)  # 按住 2 秒(仅桌面)
page = bot.press_key(page, "ctrl+w")  # 关闭当前标签页,切到另一个——重新赋值
bot.type_text(page, "", "hello", press_enter=True)  # 空 locate:直接输入到
                                    # 当前焦点元素(无 AI、无模型调用)
```

**直接输入。** `type_text` 传**空 `locate`** 会跳过 AI 定位,输入到当前
拥有键盘焦点的元素——适合焦点已就位的场景(按回车打开的游戏聊天框、Tab
切到的字段)。焦点是否正确由你负责;`press_enter` / `clear_before_typing`
仍然生效,`timeout`/`wait` 被忽略。

**`press_key` 可以传什么。** 一个名字全后端通用;各后端映射到自己的词汇:

| 类别 | 示例 | 说明 |
| --- | --- | --- |
| 单键 | `Enter` `Escape` `Tab` `Backspace` `Delete` `Space` | |
| 方向/翻页 | `ArrowUp/Down/Left/Right` `PageUp` `PageDown` `Home` `End` | |
| 组合键(桌面/浏览器) | `ctrl+c` `ctrl+a` `alt+tab` `ctrl+shift+t` | 修饰键 `ctrl` `alt` `shift` `cmd`(= meta/win);用 `+` 连接 |
| 移动端(Android/iOS) | `Back` `Home` `Menu` `Enter` | 仅单键,无组合。`Back`/`Menu` 仅限 Android;iOS(WDA)支持 `Home`、`Enter`、音量键和锁屏键,其余抛 `NotImplementedError` |
| 按住(桌面) | `duration_seconds=2`(浮点 > 0,上限 10) | 按住指定时长再释放——定量的游戏内移动(`w`、`shift+w`)。仅 pyautogui + Windows 窗口后端;web/移动端忽略并瞬时点按 |

因此 `bot.press_key(t, "Enter")` 在 Android 上自动变成 adb keycode,在
Windows 窗口后端自动变成 DirectInput 扫描码。

**智能 `go_back`(Playwright):** 当前页有历史就原地后退;没有历史——比如
点击在**新标签页**打开了链接(新标签页没有历史)——且还有其他标签页时,
就关闭当前标签页回到上一个:

```python
for i in range(4):
    page = bot.click(page, f"打开第 {i + 1} 个视频")  # 打开新标签页
    bot.screenshot(page)
    page = bot.go_back(page)                          # 关闭它,回到列表
```

想无视历史强制关闭当前标签页,直接用 `close_tab`。

## 平台支持矩阵

| 动作           | Playwright | Selenium | Appium(移动) | pyautogui(桌面) | adb(Android) | WDA(iOS) | Window(Windows) |
| -------------- | :--------: | :------: | :-------------: | :-----------------: | :-----------: | :-------: | :--------------: |
| `click`        |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |
| `double_click` |     ✅     |    ✅    |      ✅ ᵃ       |         ✅          |     ✅ ᵃ      |    ✅     |        ✅        |
| `right_click`  |     ✅     |    ✅    |    = 点按 ᵇ     |         ✅          |    = 点按 ᵇ   |  = 点按 ᵇ |        ✅        |
| `hover`        |     ✅     |    ✅    |    空操作 ᶜ     |         ✅          |    空操作 ᶜ   |  空操作 ᶜ |        ✅        |
| `type_text`    |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |
| `clear_text`   |     ✅     |    ✅    |       ✅        |         ✅          |     ✅ ᵈ      |   ✅ ᵈ    |        ✅        |
| `press_key`    |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |       ✅ ᵉ       |
| `scroll`       |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |
| `drag`         |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |
| `long_press`   |     ❌ ᶠ    |    ❌ ᶠ   |       ✅        |         ❌ ᶠ         |      ✅       |    ✅     |       ❌ ᶠ        |
| `mouse_down`   |     ❌ ᵍ    |    ❌ ᵍ   |       ❌ ᵍ      |         ✅          |     ❌ ᵍ      |   ❌ ᵍ    |        ✅        |
| `mouse_up`     |     ❌ ᵍ    |    ❌ ᵍ   |       ❌ ᵍ      |         ✅          |     ❌ ᵍ      |   ❌ ᵍ    |        ✅        |
| `key_down`     |     ❌ ᵍ    |    ❌ ᵍ   |       ❌ ᵍ      |         ✅          |     ❌ ᵍ      |   ❌ ᵍ    |        ✅        |
| `key_up`       |     ❌ ᵍ    |    ❌ ᵍ   |       ❌ ᵍ      |         ✅          |     ❌ ᵍ      |   ❌ ᵍ    |        ✅        |
| `navigate`     |     ✅     |    ✅    |       ✅        |         ❌          |      ❌       |    ❌     |        ❌        |
| `go_back`      |     ✅     |    ✅    |       ✅        |         ❌          |      ✅       |   ✅ ʰ    |        ❌        |
| `close_tab`    |     ✅     |    ❌    |       ❌        |         ❌          |      ❌       |    ❌     |        ❌        |
| `screenshot`   |     ✅     |    ✅    |       ✅        |         ✅          |      ✅       |    ✅     |        ✅        |

AI 定位的动作(`click`、`type_text`、`double_click`)和 AI 操作
(`extract`、`verify`、`wait_for`、`ai`)在**所有**框架上可用——矩阵展示的
是各底层动作在每个平台的映射方式。

- ᵃ 触屏平台用两次快速点按模拟 `double_click`。
- ᵇ 移动端没有右键:降级为点按。
- ᶜ 触屏目标没有悬停:移动端为空操作。
- ᵈ 纯 adb/WDA 没有元素模型;`clear_text` 是尽力而为(Android:光标到末尾 + 连续删除;iOS:连续退格)。
- ᵉ Windows 窗口后端发送 DirectInput 扫描码(真正的硬件级按键,含 `ctrl`/`alt`/`win` 组合);扫描码表之外的字符以 unicode 键事件注入。`duration_seconds`(按住)仅在 pyautogui + Windows 窗口后端生效;其他平台降级为瞬时点按。
- ᶠ `long_press` 是触屏专属手势(Android/iOS)。浏览器/桌面 adapter 抛 `NotImplementedError`。
- ᵍ `mouse_down`/`mouse_up`/`key_down`/`key_up` 是桌面专属的按下/释放拆分原语(pyautogui + Windows 窗口后端),用于在执行其他动作时保持输入按住。按下与释放需配对;`ai()` 运行结束和 `close()` 时自动释放仍按住的输入。`mouse_up` 的 locate 可省略(省略则在当前光标处释放——确定性、无 AI)。浏览器/移动端 adapter 抛 `NotImplementedError`。
- ʰ iOS 没有返回键;`go_back` 执行通用的左边缘右滑手势。

`navigate`/`go_back` 在不支持的平台抛 `NotImplementedError`;`close_tab`
仅 Playwright 支持,因此 `go_back` 的新标签页回退逻辑也仅适用于
Playwright——Selenium/Appium 上 `go_back` 始终是历史后退,Android 上映射为
`keyevent BACK`。

## 截图(无 AI)

保存到 `report_dir/screenshots/` 并返回保存路径(`report=False` 时返回
`None`):

```python
path = bot.screenshot(page)
```

## 启动桌面应用(无 AI)

pyautogui 能驱动鼠标键盘但打不开应用。`launch_app` 调用操作系统,让桌面
运行从确定的应用开始:

```python
bot.launch_app("WeChat")             # macOS:应用名(或 bundle id)
# launch_app("notepad")              # Windows:exe 路径、注册名或 UWP AppUserModelID
# launch_app("/path/to/app", wait=3) # 等窗口出现的秒数(默认 2)
```

macOS 用 `open -a`/`open -b`(已运行的应用会被激活),Windows 用
`os.startfile`/`start`/`explorer.exe shell:AppsFolder`,Linux 直接执行。
也可独立导入:`from qirabot import launch_app`。

## 任务生命周期

每个 `Qirabot` 实例管理一个跟踪所有操作的本地运行:构造时创建,并分配一个
本地运行 id(`bot.task_id`,形如 `local-<8 位十六进制>`),每次
`click()` / `extract()` / `ai()` 记录为一个步骤,`close()` 或上下文管理器
退出时标记完成。所有运行记录都留在你的机器上——只用于生成 HTML 报告:

```python
with Qirabot(task_name="my automation") as bot:
    page = bot.open("https://example.com")
    print(bot.extract(page, "提取主标题"))
# 自动调用 bot.close()
```

忘了 `close()` 有 `atexit` 兜底,在脚本退出时清理。

需要以“completed”之外的终态结束运行时,还有两个生命周期调用:
`bot.fail("哪里出了问题")` 把运行记录为失败,`bot.cancel("原因")` 把运行
记录为取消——都用在 `close()` 默认记录的“成功完成”之前,或代替它。终态
会体现在该次运行的 HTML 报告里。

另见:[配置](/zh/advanced/configuration)(构造参数、模型选择、settle
延迟) · [错误处理](/zh/advanced/error-handling) ·
[自定义 Adapter](/zh/backends/custom-adapters)(`bind()`、`DeviceAdapter`)
