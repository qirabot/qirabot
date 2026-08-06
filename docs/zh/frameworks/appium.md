---
title: 给 Appium 测试加上 AI——Android 与 iOS 的视觉定位
description: 用 AI 视觉驱动现有 Appium 会话——不写 UiAutomator 或 XCUITest 选择器,兼容云真机平台,支持会话录屏,Android 和 iOS driver 通用。
---

# Appium + Qirabot

如果你已经在跑 Appium,不论是本地服务器还是云真机平台,Qirabot 都可以
直接挂载到现有的 driver 上。元素定位变成对截图的 AI 视觉识别,所以
Flutter、React Native、WebView、Unity 视图和原生界面的自动化写法完全
一样,也不需要再维护 UiAutomator / XCUITest 选择器。

```python
from appium import webdriver
from appium.options.android import UiAutomator2Options
from qirabot import Qirabot

options = UiAutomator2Options()
options.platform_name = "Android"
options.device_name = "emulator-5554"
options.app_package = "com.android.settings"
options.app_activity = ".Settings"
driver = webdriver.Remote("http://localhost:4723", options=options)
bot = Qirabot().bind(driver)

bot.click("Wi-Fi 设置")
result = bot.ai("打开显示设置,把字体大小改为“大”")
print(f"Success: {result.success}")
bot.close()
driver.quit()
```

需要 extra:

```bash
pip install "qirabot[appium]"
```

iOS driver(XCUITest options)同样适用:`bind()` 能同时识别 Android 和
iOS 的 Appium 会话。

## CLI:选择 Appium 引擎

传 `--appium-url` 即把 CLI 从内置直连后端切换到你的 Appium 服务器:

```bash
qirabot android "清除所有通知" --appium-url http://localhost:4723
qirabot ios "..." --device "iPhone 15"      # 模拟器设备类型(选择 Appium)
```

注意:CLI 的 Appium iOS 引擎面向**模拟器**(`--device` 是
`xcrun simctl list devicetypes` 里的设备类型)。iPhone 真机更适合走
[WDA 直连后端](/zh/backends/ios),那条路完全不需要 Appium。

## 录制设备屏幕

`record_device=True` 使用 Appium 的会话录屏 API,Android 和 iOS driver
都支持:

```python
bot = Qirabot(record_device=True).bind(driver)
bot.ai("走完新手引导流程")
bot.stop_recording()   # 在 driver.quit() 之前调用——视频存在会话里
```

视频落在 `report_dir/recording.mp4` 并嵌入 HTML 运行报告。

## Appium 还是内置后端?

| | Appium 引擎 | 内置(adb / WDA) |
|---|---|---|
| 需要服务器 | 是 | 否 |
| 设备云(BrowserStack、Sauce…) | 支持 | 不支持 |
| 设备端安装 | Appium 装 UiAutomator2/WDA | 无(Android)/ WDA(iOS) |
| 额外包 | `qirabot[appium]` | 无 |
| 手势 | Appium 全集 | tap/swipe/keyevent 级别 |

经验法则:如果已经在 Appium 上有投入,或者需要设备云,就留在 Appium,
在上面加 Qirabot;如果是本地设备从零开始,
[Android adb 后端](/zh/backends/android)和
[iOS WDA 后端](/zh/backends/ios)的活动部件更少。

相关:[Android 后端](/zh/backends/android) · [iOS 后端](/zh/backends/ios)
