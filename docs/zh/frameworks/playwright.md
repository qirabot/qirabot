---
title: 给 Playwright 测试加上 AI——视觉断言与自愈步骤
description: 在现有 Playwright 套件中注入 AI 视觉——自然语言定位、bot.verify() 视觉断言、数据提取,以及与你的选择器并存的 bot.ai() 自主步骤。
---

# Playwright + Qirabot

你的 Playwright 套件可以原样保留,选择器、fixture、CI 都不用动。只在
选择器不好写的地方加 AI 就够了:动态内容、canvas、第三方组件,以及那些
关心"页面看起来怎样"而不是"DOM 里有什么"的断言。

```python
from playwright.sync_api import sync_playwright
from qirabot import Qirabot

bot = Qirabot()

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("https://github.com/trending")

    # 你的选择器和 AI 步骤自由穿插
    repos = bot.extract(page, "提取前 5 个热门仓库的名字")
    print(repos)

    browser.close()
bot.close()
```

不需要额外配置,每个 Qirabot 动作的第一个参数就是 Playwright 的 `page`。

## 各类调用的价值

- `bot.verify(page, "购物车显示 1 件商品")`:用视觉断言取代
  element-exists 断言。改标记、改文案、重构 CSS 都不影响它。
- `bot.extract(page, "把结果列表里的价格提取为 JSON 数组")`:直接从
  渲染后的页面拿结构化数据,不用写解析逻辑。
- `bot.click(page, "登录按钮")`:没有稳定选择器时,用自然语言定位。
- `bot.ai(page, "以 John Doe、邮编 10001 完成结算")`:把整段容易出问题的
  流程交给 AI,之后断言 `result.success`。

## 新标签页:重新赋值返回的 page

点击可能打开新标签页;`click` / `type_text` / `press_key` 的返回值就是
下一步原生调用应该用的 page。Playwright 下保持显式写法(而不是
`bind()`),让标签页切换始终可见:

```python
page = bot.click(page, "打开第一个视频")   # 可能返回新标签页
page.fill("#comment", "nice")              # 原生调用落在正确的 page 上

for i in range(4):
    page = bot.click(page, f"打开第 {i + 1} 个视频")
    bot.screenshot(page)
    page = bot.go_back(page)   # 智能:关闭无历史的新标签页,回到列表
```

用 `bot.press_key(page, "ctrl+w")` 关闭标签页时同样会切换活动标签页,
处理方式相同:重新赋值。如果确实用了绑定代理,当前活动页面可以通过
`bot.current_page()` 获取。

## 自动等待

AI 定位的动作会轮询到元素出现再执行:

```python
bot.click(page, "登录按钮", timeout=15.0, interval=2.0)
bot.wait_for(page, "仪表盘已加载完成", timeout=15.0)
```

你的原生调用仍由 Playwright 自己的 auto-waiting 处理;Qirabot 在
Playwright 上不额外加 settle 延迟(信任框架)。

## 底层机制

决策引擎在 SDK 内本地运行:截图直接从你的机器发往你配置的模型端点
(Google Vertex AI)做推理和元素定位;动作通过你的 Playwright 会话在
本地执行。代码、cookie、凭据都不会离开你的机器,发给模型的只有截图和
指令文本。

相关:[浏览器后端](/zh/backends/browser)(托管浏览器、CDP 接管、持久化
profile) · [pytest 集成](/zh/frameworks/pytest)
