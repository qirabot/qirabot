---
title: 自定义 Adapter 与框架挂载
description: 把 Qirabot 挂载到任意自动化栈——Playwright、Selenium、Appium、pyautogui;或用 7 个原语实现 DeviceAdapter,驱动云真机平台和自定义引擎。含 Airtest 迁移指南。
---

# 自定义 Adapter 与挂载

Qirabot 的设计目标是加入你已有的技术栈,而不是替换它。每个动作的第一个
参数就是框架对象(`page` / `driver` / 设备 / 模块)。把你自己的对象传
进来,AI 步骤就能和现有代码自由混合:

| 你在用 | 传入 | 说明 |
|---|---|---|
| [Playwright](/zh/frameworks/playwright) | `page` | 保持显式写法:点击可能返回新标签页 |
| [Selenium](/zh/frameworks/selenium) | `driver` | `uv pip install qirabot selenium` |
| [Appium](/zh/frameworks/appium) | `driver` | `qirabot[appium]`;Android 和 iOS |
| pyautogui | `pyautogui` 模块 | `qirabot[desktop]`,见[桌面](/zh/backends/desktop) |
| 内置设备 | `AdbDevice` / `WdaClient` / `Window` | 无需 extras |

如果在 [pytest](/zh/frameworks/pytest) 里跑,逻辑相同:fixture 持有
bot,测试传入自己的 `page`/`driver`。

## bind() —— 省去重复的第一个参数

整个会话只驱动一个稳定目标时,`bind()` 一次即可:

```python
bot = Qirabot().bind(driver)     # Selenium/Appium driver、pyautogui、AdbDevice/WdaClient/Window
bot.click("登录")
bot.type_text("邮箱", "a@b.com")

with Qirabot().bind(driver) as bot:   # 也可作为上下文管理器
    ...
```

`bind()` 推荐用于设备后端、pyautogui、Appium、Selenium。Playwright 请保持
`page = bot.click(page, ...)` 的显式写法,让新标签页切换可见;用了绑定
代理时,当前活动页面可通过 `bot.current_page()` 获取。

## 编写自定义 Adapter

凡是 qirabot 没有内置的后端,比如云真机 SDK、自定义引擎桥、VNC 会话,
都可以通过继承 `qirabot.DeviceAdapter` 接入。必需的原语只有:

```
screenshot · click · double_click · type_text · press_key · scroll · device_info
```

(这些就是[平台支持矩阵](/zh/reference/api#平台支持矩阵)里列出的动作。
你的 adapter 定义每个动作如何映射到你的引擎,其余能力都由此派生。)

然后要么直接把实例传给 `bind()`:

```python
bot = Qirabot().bind(MyAdapter(handle))
```

要么实现 `accepts()` 并注册一次,让 `bind()` 认识你框架的原生对象:

```python
from qirabot import register_adapter

register_adapter(MyAdapter)          # 优先于内置 adapter 检查
bot = Qirabot().bind(native_object)
```

[examples/airtest/adapter.py](https://github.com/qirabot/qirabot/blob/main/examples/airtest/adapter.py)
是一份完整的参考实现。

## 从 Airtest 迁移

qirabot 不依赖 airtest,因此没有与现代环境冲突的 `numpy<2` /
`opencv-contrib` 版本锁。内置后端可以直接替换 airtest 的设备连接方式,
对应关系如下:

```python
# airtest                                      # qirabot
connect_device("Android:///emu-5554")          AdbDevice("emu-5554")
connect_device("iOS:///http://...:8100")       WdaClient("http://...:8100")
connect_device("Windows:///132456")            Window(hwnd=132456)
```

如果想保留 airtest 脚本,把上面的参考 adapter 复制进你的项目(airtest 是
*你的*依赖,不是 qirabot 的),`register_adapter` 注册一次,
`bind(connect_device(...))` 调用即可原样运行。
