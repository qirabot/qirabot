---
layout: home
title: Qirabot — AI Vision GUI Automation for Browsers, Mobile, Desktop & Games
description: Cross-platform GUI automation driven by multimodal AI vision. Describe UI elements in plain English — no DOM, no selectors. Python SDK and CLI for Chrome, Android, iOS, Windows, and games.

hero:
  name: Qirabot
  text: GUI automation driven by AI vision
  tagline: Drive browsers, mobile apps, desktops, and games through pixels — no DOM, no selectors. Describe what you want in plain English; the AI sees the screen and acts.
  actions:
    - theme: brand
      text: Quick Start
      link: /guide/quickstart
    - theme: alt
      text: Installation
      link: /guide/installation
    - theme: alt
      text: GitHub
      link: https://github.com/qirabot/qirabot
  image:
    src: /logo.svg
    alt: Qirabot

features:
  - icon: 🌐
    title: Browser automation
    details: Playwright under the hood — bot.open() launches Chromium for you, or attach to a running Chrome over CDP. Headless, persistent profiles, remote pools.
    link: /backends/browser
  - icon: 🤖
    title: Android without Appium
    details: Direct over adb — no server, nothing installed on the device. Screenshot, tap, swipe, and type (including Chinese and emoji) on any device adb can see.
    link: /backends/android
  - icon: 📱
    title: iOS without Appium
    details: Talks HTTP straight to WebDriverAgent on a real iPhone. No Appium server, no extra packages — just iproxy and a WDA session.
    link: /backends/ios
  - icon: 🎮
    title: Windows windows & games
    details: Bind one window by title or HWND. Input is DirectInput scancodes — the level games actually poll, which virtual-key automation can't reach. Drives Unity, Unreal, and native apps.
    link: /backends/windows-games
  - icon: 🔌
    title: Bolt onto your stack
    details: Already running Playwright, Selenium, Appium, or pyautogui? Pass your page or driver object and inject AI steps into the suite you have — no rewrite.
    link: /backends/custom-adapters
  - icon: 🧩
    title: One API everywhere
    details: bot.ai() runs whole tasks autonomously; bot.click() / extract() / verify() give you deterministic steps with AI element location. Same calls on every platform.
    link: /guide/quickstart
---

## What is Qirabot?

Qirabot is a Python SDK and CLI for **vision-based GUI automation**. Instead of
querying a DOM or an accessibility tree, it looks at the actual pixels on
screen: a multimodal AI model reads the screenshot, finds the element you
described in natural language, and returns coordinates for the client to act
on. That means it works on surfaces traditional frameworks can't touch —
canvas-rendered UIs, native mobile apps, desktop applications, and full 3D
games.

The decision engine runs locally inside the SDK: screenshots go directly
from your machine to the model endpoint you configure on Google Vertex AI —
no Qirabot account, no API key, no intermediary server.

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://www.wikipedia.org")

result = bot.ai(page, "Search for SpaceX and get the first sentence of the article")
print(result.output)

bot.close()
```

Two ways to drive it:

- **Autonomous** — `bot.ai(page, "do the whole task")`: the AI sees the
  screen, decides the next action, and loops until the goal is met.
- **Deterministic** — `bot.click(page, "Login button")`,
  `bot.extract(...)`, `bot.verify(...)`: you own the control flow, AI vision
  locates each element. No XPath, no CSS selectors, nothing to break when the
  layout shifts.

Every run writes an HTML report with per-step screenshots, and `--record`
captures a video. Start with the [Quick Start](/guide/quickstart), or jump to
the backend you're automating: [Browser](/backends/browser),
[Android](/backends/android), [iOS](/backends/ios),
[Windows & Games](/backends/windows-games), [Desktop](/backends/desktop).
