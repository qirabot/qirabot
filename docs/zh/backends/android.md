---
title: Android 免 Appium 自动化——adb 直连
description: 用 AI 视觉自动化 Android 真机和模拟器,无需 Appium 服务器、设备端零安装。基于纯 adb 的 Python SDK 与 CLI,支持中文/emoji 输入与设备录屏。
---

# Android —— adb 直连

Qirabot 内置的 Android 后端直接通过 adb 与设备通信:截图走 `screencap`,
输入走 `input tap/swipe/keyevent`。不需要运行 Appium 服务器,也不依赖
任何框架,设备上不用安装任何东西——只有一个例外:非 ASCII 输入会装一个
输入法(见下文)。凡是能出现在 `adb devices` 列表里的真机或模拟器都可以
驱动。

元素定位是对截图做 AI 视觉识别,所以没有 UiAutomator 选择器,也不依赖
无障碍树。原生 App、WebView、Flutter、React Native 和游戏都用同一套
方式处理。

不需要任何 Python extra,但宿主机上要有 `adb`:安装 Android
[platform-tools](https://developer.android.com/tools/releases/platform-tools)
并加入 PATH。`qirabot doctor` 会检查这一项。最快的验证方式是 CLI:

```bash
qirabot android "打开设置并开启飞行模式"
qirabot android "..." -d emulator-5554 --app-package com.android.settings
```

同样的事在 Python 里:

```python
from qirabot import AdbDevice, Qirabot

device = AdbDevice()                 # 或 AdbDevice(serial="emulator-5554")
bot = Qirabot().bind(device)

bot.click("登录按钮")                # AI 定位——不需要模板图片
result = bot.ai("打开设置并开启深色模式")
print(f"Success: {result.success}")
bot.close()
```

`bind(device)` 一次性固定目标,之后的每个调用都省去第一个参数
(`bot.click("...")` 而不是 `bot.click(device, "...")`)。细节见
[自定义 Adapter 与挂载](/zh/backends/custom-adapters)。

## 纯 ASCII 之外的输入(中文、emoji、`%`)

`input text` 只能传纯可打印 ASCII,其余情况 `bot.type_text(...)` 都会切到
内置的 ADBKeyboard 输入法:非 ASCII 文本(中文、emoji)、`\n` `\t` 等控制
字符,以及 `%`——`input text` 会把它当成格式化序列展开。后两类意味着
`"50% off"` 这种看着是纯 ASCII 的字符串同样会走输入法路径。

输入法在首次用到时装进设备,`close()` 时切回你原来的键盘。
APK 会一直留在设备上;没走到 `close()` 的运行(比如脚本崩溃)会让设备
停在 ADBKeyboard 上,在系统设置里切回即可。设备被 MDM 策略禁止安装应用
时,需要预装它,或改用 Appium 引擎(`--appium-url`)。

## 设备录屏

把设备屏幕(而不是宿主机屏幕)录进运行报告:

```python
bot = Qirabot(record_device=True)   # 或 QIRA_RECORD_DEVICE=1
bot.ai(device, "打开设置")
bot.close()                         # 视频自动拉取到 report_dir/recording.mp4
```

底层是 `adb screenrecord`;超过其 3 分钟上限的运行会用 ffmpeg 合并分段。
CLI 写法:`qirabot android "..." --record`。

## 改走 Appium

如果你已经有 Appium 环境或云真机平台,同一套 API 也能驱动 Appium
driver。安装 `qirabot[appium]`,然后传入 driver:

```python
from appium import webdriver
from appium.options.android import UiAutomator2Options
from qirabot import Qirabot

options = UiAutomator2Options()
options.platform_name = "Android"
options.device_name = "emulator-5554"
driver = webdriver.Remote("http://localhost:4723", options=options)
bot = Qirabot().bind(driver)

result = bot.ai("打开显示设置,把字体大小改为“大”")
bot.close()
driver.quit()
```

CLI 传 `--appium-url` 即选择 Appium 引擎:
`qirabot android "..." --appium-url http://localhost:4723`。完整的 Appium
工作流(云真机平台、录屏、以及 Appium 与内置后端的对比)见
[Appium + Qirabot](/zh/frameworks/appium)。

## 平台说明

- `press_key("Back")` / `"Home"` / `"Menu"` 映射为 adb keyevent;
  `go_back` 发送 `keyevent BACK`。
- `long_press` 可用(触屏平台);`hover` 为空操作,`right_click` 降级为
  点按。
- 纯 adb 下 `clear_text` 是尽力而为(光标移到末尾 + 连续删除):这里
  刻意没有引入元素模型。
- 如果从 Airtest 迁移,`connect_device("Android:///emu-5554")` 改为
  `AdbDevice("emu-5554")`,其余 `bind()` 代码不变。
- 每个动作的完整行为见
  [平台支持矩阵](/zh/reference/api#平台支持矩阵)。
