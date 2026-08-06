---
title: Add AI to Appium Tests — Vision Locators for Android & iOS
description: Drive an existing Appium session with AI vision - no UiAutomator or XCUITest selectors, works with device clouds, session recording, and both Android and iOS drivers.
---

# Appium + Qirabot

If you already run Appium, against a local server or a cloud device farm,
Qirabot attaches to the driver you have. Element location becomes AI
vision on the screenshot, so Flutter, React Native, WebViews, Unity views,
and native screens are all automated the same way, and there is no
UiAutomator or XCUITest selector maintenance.

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

bot.click("Wi-Fi settings")
result = bot.ai("Open Display settings and change font size to Large")
print(f"Success: {result.success}")
bot.close()
driver.quit()
```

Requires the extra:

```bash
pip install "qirabot[appium]"
```

The same works for iOS drivers (XCUITest options): `bind()` recognizes
both Android and iOS Appium sessions.

## CLI: selecting the Appium engine

Passing `--appium-url` switches the CLI from the built-in direct backends to
your Appium server:

```bash
qirabot android "Clear all notifications" --appium-url http://localhost:4723
qirabot ios "..." --device "iPhone 15"      # simulator device type (selects Appium)
```

Note that the CLI's Appium iOS engine targets simulators: `--device` is a
device type from `xcrun simctl list devicetypes`. Real iPhones are better
served by the [WDA-direct backend](/backends/ios), which does not use
Appium at all.

## Recording the device screen

`record_device=True` uses Appium's session recording API, which works for
both Android and iOS drivers:

```python
bot = Qirabot(record_device=True).bind(driver)
bot.ai("run through the onboarding flow")
bot.stop_recording()   # call before driver.quit() — the video lives in the session
```

The video lands in `report_dir/recording.mp4` and is embedded in the HTML
run report.

## Appium or the built-in backends?

| | Appium engine | Built-in (adb / WDA) |
|---|---|---|
| Server required | yes | no |
| Device clouds (BrowserStack, Sauce…) | yes | no |
| Install on device | UiAutomator2/WDA via Appium | nothing (Android) / WDA (iOS) |
| Extra package | `qirabot[appium]` | none |
| Gestures | full Appium set | tap/swipe/keyevent level |

As a rule of thumb: if you are already invested in Appium or need a device
cloud, stay on Appium and add Qirabot on top. If you are starting fresh on
local devices, the [Android adb backend](/backends/android) and
[iOS WDA backend](/backends/ios) involve fewer moving parts.

Related: [Android backend](/backends/android) · [iOS backend](/backends/ios)
