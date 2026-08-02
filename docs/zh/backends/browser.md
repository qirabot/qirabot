---
title: Python AI 浏览器自动化(Playwright)——不写选择器
description: 用 AI 视觉代替 CSS 选择器驱动 Chrome——Qirabot 可自动启动 Chromium,也能挂载到你现有的 Playwright 或 Selenium 会话。支持 headless、持久化 profile、CDP 接管。
---

# 浏览器自动化

Qirabot 通过**像素而非 DOM** 驱动浏览器。AI 像人一样阅读渲染后的页面,
所以选择器方案会挂的地方它都能工作:canvas 应用、跨域 iframe、shadow
DOM、频繁 A/B 测试的布局,以及改版速度快过测试套件的页面。

可以让 Qirabot 托管浏览器,也可以挂载到你已有的 Playwright / Selenium
会话上。

## 托管浏览器

需要 `browser` extra:`pip install "qirabot[browser]"`,然后
`qirabot install-browser`。

最快的验证方式是 CLI——一条命令,零代码:

```bash
qirabot browser "打开热度最高的帖子并总结讨论内容" --url news.ycombinator.com
qirabot browser "..." --headless --viewport 1920x1080
qirabot browser "..." --user-data-dir ~/.qira-profile --channel chrome   # 登录态跨运行保留
qirabot browser "..." --cdp-url http://localhost:9222                    # 接管已运行的 Chrome
```

`--cdp-url` 也适用于 browserless 之类的远程浏览器池。

### 登录一次,后续复用

需要账号的站点,登录这一步手动做——不跑 AI 任务、不调用模型、不花
token。`open-browser` 会打开该 profile 的可见浏览器窗口;登录后关闭窗口,
之后所有传入同一个 `--user-data-dir` 的运行都直接带着登录态启动:

```bash
qirabot open-browser --user-data-dir ~/.qira-profile --url news.ycombinator.com/login
# 在窗口里完成登录,然后关闭它
qirabot browser "打开热度最高的帖子并总结讨论内容" --user-data-dir ~/.qira-profile
```

同一个 profile 目录不能被两个浏览器同时占用,跑任务前先关掉登录窗口。
如果是任务**中途**撞上的登录墙(验证码、二次验证),见
[human-in-the-loop](/zh/advanced/ai-tasks#人工介入-human-in-the-loop)。

同样的运行走 SDK——`bot.open()` 自动启动 Chromium(底层为 Playwright),
你不需要写任何框架代码:

```python
from qirabot import Qirabot

bot = Qirabot()
page = bot.open("https://news.ycombinator.com")

result = bot.ai(page, "打开热度最高的帖子并总结讨论内容")
print(result.output)

bot.close()
```

## 挂载到你已有的会话

已经在自己的框架里跑着浏览器?跳过 `bot.open()`,把你自己的对象作为目标
传入——或 `bind()` 一次,省去重复传参(`bind()` 详见
[自定义 Adapter 与挂载](/zh/backends/custom-adapters)):

- **Playwright** —— 传入你的 `page`;你的选择器和 AI 步骤自由混用。
  完整指南:[Playwright + Qirabot](/zh/frameworks/playwright)。
- **Selenium** —— 传入(或 `bind()`)你的 `driver`;不是 extra,自带
  即可(`pip install qirabot selenium`)。完整指南:
  [Selenium + Qirabot](/zh/frameworks/selenium)。
- **pytest** —— 在现有测试套件里加 AI 断言和 AI 步骤,含 fixture 与 CI
  说明。完整指南:[pytest + Qirabot](/zh/frameworks/pytest)。

有一个值得提前知道的坑:点击可能打开**新标签页**,返回的 page 才是活动
的那个——保持 `page = bot.click(page, ...)` 的写法。细节和智能 `go_back`
行为见
[API 参考](/zh/reference/api#导航、滚动与按键-无-ai)。

## 说明

- headless 检测:无显示器环境(无 `DISPLAY`)下,`bot.open()` 和 CLI 自动
  切换 headless 并给出警告。`open-browser` 是例外——它会直接报错,因为
  看不见的浏览器没法手动登录。
- `close_tab` 仅 Playwright 支持;`navigate`、`go_back`、`press_key`(含
  `ctrl+w` 关闭当前标签页——记得重新赋值返回的 page)和 `scroll` 均可
  用。完整的平台动作矩阵见 [API 参考](/zh/reference/api#平台支持矩阵)。
