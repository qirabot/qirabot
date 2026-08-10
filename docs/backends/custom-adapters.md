---
title: Custom Adapters & Bolt-On Integration
description: Bolt Qirabot onto any automation stack — Playwright, Selenium, Appium, pyautogui — or write a DeviceAdapter with 7 primitives to drive cloud device farms and custom engines.
---

# Custom Adapters & Bolt-On

Qirabot is designed to join the stack you already have, not replace it.
Every action takes the framework object (`page` / `driver` / device / module)
as its first argument. Pass yours and mix AI steps with your existing code:

| You run | You pass | Notes |
|---|---|---|
| [Playwright](/frameworks/playwright) | `page` | keep the explicit form; clicks can return a new tab |
| [Selenium](/frameworks/selenium) | `driver` | `uv pip install qirabot selenium` |
| [Appium](/frameworks/appium) | `driver` | `qirabot[appium]`; Android and iOS |
| pyautogui | the `pyautogui` module | `qirabot[desktop]`; see [Desktop](/backends/desktop) |
| Built-in devices | `AdbDevice` / `WdaClient` / `Window` | no extras |

If you run inside [pytest](/frameworks/pytest), the same applies: the
fixture holds the bot, and your tests pass their own `page`/`driver`.

## bind() — drop the repeated argument

When you drive a single, stable target for the whole session, `bind()` once:

```python
bot = Qirabot().bind(driver)     # Selenium/Appium driver, pyautogui, AdbDevice/WdaClient/Window
bot.click("Login")
bot.type_text("Email", "a@b.com")

with Qirabot().bind(driver) as bot:   # works as a context manager too
    ...
```

`bind()` is recommended for the device backends, pyautogui, Appium, and
Selenium. For Playwright keep `page = bot.click(page, ...)` so new-tab
follows stay visible; with a bound proxy, reach the live page via
`bot.current_page()`.

## Writing a custom adapter

Anything qirabot doesn't ship (cloud-device SDKs, custom engine bridges, a
VNC session) plugs in by subclassing `qirabot.DeviceAdapter`. The required
primitives are:

```
screenshot · click · double_click · type_text · press_key · scroll · device_info
```

(These are the same actions listed in the
[platform support matrix](/reference/api#platform-support-matrix); your
adapter defines how each maps to your engine, and everything else is
derived.)

Then either pass an instance straight to `bind()`:

```python
bot = Qirabot().bind(MyAdapter(handle))
```

or implement `accepts()` and register once so `bind()` recognizes your
framework's native objects:

```python
from qirabot import register_adapter

register_adapter(MyAdapter)          # checked before the built-ins
bot = Qirabot().bind(native_object)
```

[examples/airtest/adapter.py](https://github.com/qirabot/qirabot/blob/main/examples/airtest/adapter.py)
is a complete reference implementation.

## Migrating from Airtest

qirabot has no airtest dependency, so none of the `numpy<2` /
`opencv-contrib` pins that collide with modern environments. The built-in
backends are drop-in replacements for airtest's device connections:

```python
# airtest                                      # qirabot
connect_device("Android:///emu-5554")          AdbDevice("emu-5554")
connect_device("iOS:///http://...:8100")       WdaClient("http://...:8100")
connect_device("Windows:///132456")            Window(hwnd=132456)
```

If you want to keep your airtest scripts, copy the reference adapter above
into your project (airtest stays your dependency, not qirabot's), call
`register_adapter` once, and your `bind(connect_device(...))` calls run
unchanged.
