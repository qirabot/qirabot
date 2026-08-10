---
title: iOS 免 Appium 自动化——直连 WebDriverAgent
description: 通过 HTTP 直连 WebDriverAgent,用 AI 视觉自动化 iPhone 真机——无需 Appium 服务器、无需额外包。含 Xcode 与 iproxy 配置步骤、MJPEG 设备录屏。
---

# iOS —— 直连 WebDriverAgent

多数 iOS 自动化方案都要在你和手机之间架一台 Appium 服务器。Qirabot 内置
的 WDA 客户端跳过了这一层,通过 HTTP 直连设备上已运行的 WebDriverAgent。
不需要 Appium 和 node,也不需要额外的 Python 包,核心安装即可使用。

元素定位是对截图做 AI 视觉识别,因此没有 XCUITest 元素查询要维护;抗拒
无障碍检查的应用(游戏、自绘 UI)和原生应用一样能自动化。

最快的验证方式是 CLI(WDA 需已在运行,配置见下文):

```bash
qirabot ios "在微信里给 Alice 发一句 hi" --bundle-id com.tencent.xin
```

同样的事在 Python 里:

```python
from qirabot import Qirabot, WdaClient

client = WdaClient("http://127.0.0.1:8100")
client.app_launch("com.apple.Preferences")
bot = Qirabot().bind(client)

result = bot.ai("打开 通用 > 关于本机,报告 iOS 版本号")
print(f"Success: {result.success}")
bot.close()
```

## 真机配置(3 步)

1. **在手机上运行 WebDriverAgent** 并保持运行。WebDriverAgent 是 Appium
   开源的 iOS agent:克隆
   [appium/WebDriverAgent](https://github.com/appium/WebDriverAgent),在
   Xcode 中打开,用你自己的签名团队对设备运行 `WebDriverAgentRunner`
   scheme(或
   `xcodebuild ... -destination 'id=<udid>' -allowProvisioningUpdates test`)。
   你只需要 agent 本身,不需要 Appium 服务器。
2. **USB 端口转发:** `iproxy 8100 8100`(来自 `libimobiledevice`,macOS
   上用 `brew install libimobiledevice` 安装)。
   自检:`curl http://127.0.0.1:8100/status` 返回 JSON 即成功。
3. **运行任务**:默认的 `--wda-url`(`http://127.0.0.1:8100`)现在已指向
   手机。

设备由 `--wda-url` 选择,而不是设备名。多台设备就为每台跑一个 `iproxy`
映射到不同本地端口,用 `--wda-url` 区分。

## 模拟器

模拟器请改用 Appium 引擎:传 `--device` 加模拟器设备类型(来自
`xcrun simctl list devicetypes` 的名字,如 `iPhone 15`),Appium 会创建
并启动对应模拟器。需要 `qirabot[appium]`。

```bash
qirabot ios "..." --device "iPhone 15"
```

注意:Appium 引擎目前只支持模拟器(没有 `--udid` 选项),真机请走上面的
WDA 直连路径。如果你已经在跑 Appium(或云真机平台),见
[Appium + Qirabot](/zh/frameworks/appium)。

## 设备录屏

默认录屏抓的是宿主机屏幕,手机画面不在其中。iOS 运行请改录 WDA 的 MJPEG
流(端口 9100;USB 真机在 8100 转发之外再加一条 `iproxy 9100 9100`;宿主
机需要 ffmpeg):

```python
bot = Qirabot(record_mjpeg_url="http://127.0.0.1:9100")
```

CLI 的 `qirabot ios "..." --record` 会自动做这件事,并在启动前检查流是否
可达;`--mjpeg-url` 可覆盖默认地址。报告结构和全部录屏开关见
[报告与录屏](/zh/advanced/reports)。

## 平台说明

- iOS 没有返回键;`bot.go_back()` 执行通用的左边缘右滑手势。
- `long_press` 可用;`hover` 为空操作,`right_click` 降级为点按;
  `clear_text` 是尽力而为的连续退格(纯 WDA 下没有元素模型,这是刻意的
  设计)。
- 如果从 Airtest 迁移,`connect_device("iOS:///http://...:8100")` 改为
  `WdaClient("http://...:8100")`,`dev.driver.app_launch(...)` 改为
  `client.app_launch(...)`。
- 每个动作的完整行为见
  [平台支持矩阵](/zh/reference/api#平台支持矩阵)。
