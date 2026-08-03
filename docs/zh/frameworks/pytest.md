---
title: pytest 里的 AI 视觉断言——自愈 UI 测试
description: 在 pytest 套件中使用 Qirabot——bot.verify() 视觉断言、屏幕数据提取、bot.ai() 自主流程步骤、fixture 用法,以及每次运行自动生成的截图 HTML 报告。
---

# pytest + Qirabot

Qirabot 以库的形式融入 pytest:每个测试(或经 fixture 每个会话)一个
`Qirabot` 实例,断言基于屏幕*显示的内容*,每次运行自动生成带逐步截图的
HTML 报告——失败时也有。

## 配合 pytest-playwright

```python
from qirabot import Qirabot

bot = Qirabot(task_name="test-checkout")   # 为简洁放在模块级;更推荐
                                           # 下面会 close() 的 fixture

def test_checkout(page):          # 你现有的 pytest-playwright fixture
    page.goto("https://shop.example.com")

    # 现有的 Playwright 选择器——原样保留
    page.fill("#username", "test_user")
    page.fill("#password", "secret")
    page.click("#login-btn")

    # AI 断言——不需要知道具体文案或选择器
    assert bot.verify(page, "商品列表页已显示")

    page.click('[data-test="add-to-cart"]')
    assert bot.verify(page, "购物车角标显示 1")

    # 流程中最动态的一段交给 AI
    result = bot.ai(page, "完成结算,姓名 John Doe,邮编 10001", max_steps=8)
    assert result.success
```

## 作为 fixture

```python
import pytest
from qirabot import Qirabot

@pytest.fixture(scope="session")
def bot():
    b = Qirabot(report_dir="./artifacts")
    yield b
    b.close()          # 写 HTML 报告

def test_search(bot, page):
    page.goto("https://www.wikipedia.org")
    bot.type_text(page, "搜索框", "SpaceX", press_enter=True)
    assert bot.verify(page, "SpaceX 词条已显示")
```

测试进程硬崩时还有 `atexit` 兜底调用 `close()`。引擎在你的进程里本地
运行,步骤之间的长时间等待是安全的——见
[配置](/zh/advanced/configuration)。

## 断言模式

```python
# 布尔检查——从不抛异常,适合 assert
assert bot.verify(page, "错误横幅【不】可见")

# 卡点——超时抛 QirabotTimeoutError,轮询直到成立
bot.wait_for(page, "加载动画已消失", timeout=15.0)

# 数值断言走提取——extract() 返回字符串子类
count = bot.extract(page, "购物车角标上的数字,返回整数")
assert int(count) == 1
```

优先用 `wait_for` 而不是 sleep:条件一成立立即返回。

## CI 注意事项

- 报告:指向 artifacts 目录(`Qirabot(report_dir=...)` 或
  `QIRA_REPORT_DIR`),失败时上传 `qira_runs/`——报告里能看到每一步的
  确切截图。
- 只要断言不要报告:`Qirabot(report=False)`。
- 凭据:在 runner 上把 `GOOGLE_APPLICATION_CREDENTIALS` 指向服务账号
  JSON(标准 Google Cloud ADC),并以环境变量设置 `QIRA_MODEL` /
  `QIRA_VERTEX_PROJECT`。CLI 的退出码同样脚本友好:`0` 通过、`1` 失败、
  `130` 中断。
- headless:无显示器的 runner 上,托管浏览器自动切 headless。

相关:[Playwright](/zh/frameworks/playwright) ·
[Selenium](/zh/frameworks/selenium) · [报告与录屏](/zh/advanced/reports)
