---
title: Android Automation Without Appium — Direct over adb
description: Automate real Android devices and emulators with AI vision, no Appium server and nothing installed on the device. Python SDK and CLI over plain adb, with Chinese/emoji typing and screen recording.
---

# Android — Direct over adb

The built-in Android backend talks to the device through plain adb:
screenshots via `screencap`, input via `input tap/swipe/keyevent`. There is
no Appium server or framework to run, and nothing to install on the device —
with one exception: non-ASCII typing installs a keyboard (below). Any real
device or emulator that appears in `adb devices` works.

Element location is AI vision on the screenshot, so there are no UiAutomator
selectors to write and no dependency on the accessibility tree. Native apps,
WebViews, Flutter, React Native, and games all look the same to it.

No Python extras — but the host does need the `adb` binary: install Android
[platform-tools](https://developer.android.com/tools/releases/platform-tools)
and put it on PATH. `qirabot doctor` checks for it. The quickest check is
the CLI:

```bash
qirabot android "Open settings and turn on airplane mode"
qirabot android "..." -d emulator-5554 --app-package com.android.settings
```

The same thing in Python:

```python
from qirabot import AdbDevice, Qirabot

device = AdbDevice()                 # or AdbDevice(serial="emulator-5554")
bot = Qirabot().bind(device)

bot.click("Login button")            # AI-located — no template images
result = bot.ai("Open Settings and turn on dark mode")
print(f"Success: {result.success}")
bot.close()
```

`bind(device)` fixes the target once, so every later call drops the first
argument (`bot.click("...")` instead of `bot.click(device, "...")`). See
[Custom Adapters & Bolt-On](/backends/custom-adapters) for the details.

## Typing beyond plain ASCII (Chinese, emoji, `%`)

`input text` only carries plain printable ASCII, so `bot.type_text(...)`
switches to the bundled ADBKeyboard IME for everything else: non-ASCII text
(Chinese, emoji), control characters like `\n` and `\t`, and `%`, which
`input text` would expand as a format sequence. The last two mean an
otherwise plain-ASCII string like `"50% off"` takes the IME path too.

The IME is installed on the device the first time it is
needed, and `close()` switches the keyboard back to your own. The APK stays
installed, and a run that never reaches `close()` (a crash, say) leaves
ADBKeyboard active — switch back in the system settings. Where app installs
are blocked by MDM policy, preinstall it or use the Appium engine
(`--appium-url`).

## Screen recording

Record the device screen (not the host) into the run report:

```python
bot = Qirabot(record_device=True)   # or QIRA_RECORD_DEVICE=1
bot.ai(device, "open settings")
bot.close()                         # pulls the video into report_dir/recording.mp4
```

Under the hood it's `adb screenrecord`; runs longer than screenrecord's
3-minute cap are merged with ffmpeg. From the CLI: `qirabot android "..." --record`.

## Through Appium instead

If you have an existing Appium setup or a cloud device farm, the same API
drives an Appium driver. Install `qirabot[appium]` and pass the driver:

```python
from appium import webdriver
from appium.options.android import UiAutomator2Options
from qirabot import Qirabot

options = UiAutomator2Options()
options.platform_name = "Android"
options.device_name = "emulator-5554"
driver = webdriver.Remote("http://localhost:4723", options=options)
bot = Qirabot().bind(driver)

result = bot.ai("Open Display settings and change font size to Large")
bot.close()
driver.quit()
```

On the CLI, passing `--appium-url` selects the Appium engine:
`qirabot android "..." --appium-url http://localhost:4723`. The full
Appium workflow (device clouds, recording, and an Appium-vs-built-in
comparison) is in [Appium + Qirabot](/frameworks/appium).

## Platform notes

- `press_key("Back")` / `"Home"` / `"Menu"` map to adb keyevents; `go_back`
  sends `keyevent BACK`.
- `long_press` is available (touch platforms); `hover` is a no-op,
  `right_click` degrades to a tap.
- `clear_text` over raw adb is best-effort (caret-to-end + repeated delete);
  there is no element model on purpose.
- If you are coming from Airtest, `connect_device("Android:///emu-5554")`
  becomes `AdbDevice("emu-5554")`; the rest of your `bind()` code is
  unchanged.
- Full per-action behavior:
  [platform support matrix](/reference/api#platform-support-matrix).
