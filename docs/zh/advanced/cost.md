---
title: 控制成本——一步花在哪里,哪些开关真正有效
description: Qirabot 每一步的 token 究竟花在工具 schema、system prompt 还是截图上,以及真正有效的杠杆:media_resolution、exclude_tools、thinking_level、用确定性步骤替代 ai(),还有如何度量花费。
---

# 控制成本

Qirabot 本身不收费。模型调用直接从你的机器发往你的 Vertex AI 项目(或
Gemini Developer API),由 Google 按该模型的价格计费。所以"成本"只有一个
含义:每次模型调用的 token 数 × 调用次数。

## 哪些调用花钱

每一次 AI 调用都会发送截图并消耗 token:`ai()`(每步一次)、带元素描述的
`click` / `type_text` / `double_click`、`extract`、`verify`、`locate`,以及
`wait_for` 的每一次轮询。

下面这些完全不调用模型,零成本:`navigate`、`go_back`、`close_tab`、
`scroll`、`press_key`、`screenshot`、`launch_app`、`key_down` / `key_up`、
`locate` 为空的 `type_text`,以及不带 locate 的 `mouse_up`。它们在
[API 参考](/zh/reference/api)里都标注了"无 AI"。

## 一步的 token 花在哪

一次决策请求携带四部分:system prompt、工具 schema、回放的文本历史,以及
图像——当前截图加最近一张历史截图。

有用的是它们的占比。在 `media_resolution="medium"` 下实测一个 Chrome 任务,
首步约 3,400 input token:

| 组成 | ≈ token | 占比 |
|---|---|---|
| 工具 schema(Chrome 上 14 个内置工具) | 2,040 | 59% |
| system prompt | 850 | 25% |
| 当前截图(`medium`) | 520 | 15% |
| 指令文本 | 30 | 1% |

反直觉的结论:**贵的不是截图,是工具 schema**。而且它是固定成本,每一步
都要付一遍。

图像 token 完全由分辨率档位决定,与图片的像素尺寸、JPEG 质量无关:

| `media_resolution` | ≈ 图像 token(1280×800) |
|---|---|
| `low` | 273 |
| `medium` | 522 |
| `high`(默认) | 1,092 |
| `ultra_high` | 更高 |

在客户端把截图缩小或重新压缩不会有任何变化——决定权在档位。因此
`screenshot_quality` 和 `screenshot_format` **不是**成本杠杆,它们只影响
落盘文件和上传体积。

::: tip 这是量级参考,不是保证
测量环境为 Gemini 3 flash 端点、1280×800 截图。具体数值会随模型、平台的
工具集和截图长宽比变化。优化之前,先用 `bot.usage` 量一下你自己的负载。
:::

## 杠杆,按效果排序

**1. 流程已知的地方改用确定性步骤。**一次 `ai()` 运行每步一次调用,而且
每次都要重发工具 schema。一个已知的登录是三次便宜调用,交给 `ai()` 可能
变成十次昂贵调用。把 `ai()` 留给流程里真正动态的尾部。

```python
bot.type_text(page, "用户名输入框", "standard_user")
bot.type_text(page, "密码输入框", "secret_sauce")
bot.click(page, "登录按钮")
result = bot.ai(page, "以 John Doe、邮编 10001 完成结账")   # 动态部分
```

**2. 降低 `media_resolution`。**从默认的 `high` 降到 `medium`,每步省约
570 个图像 token,降到 `low` 省约 820。界面干净、对比度高、点击目标大的
场景通常 `medium` 完全够用;密集表格、小字号和游戏才值得为 `high` 付钱。

```python
bot = Qirabot(media_resolution="medium")     # 或 QIRA_MEDIA_RESOLUTION / --media-resolution
```

**3. 用 `exclude_tools` 削减工具。**最大的固定成本,恰恰是大多数人从没
动过的那个。只需要点击和输入的任务,不必在每次请求里都带上 `drag`、
`long_press`、`hover`、`scroll_at`:

```python
bot.ai(page, "…", exclude_tools=["drag", "hover", "scroll_at", "double_click"])
```

各平台内置工具数量:Chrome 14、桌面 17、Android/iOS 12、Windows 窗口 11。
`done` 不可排除。这同时也能避免模型跑去用任务根本不需要的动作。见
[AI 任务与自定义工具](/zh/advanced/ai-tasks)。

**4. `thinking_level` 保持 `low`,只抬高难判断的调用。**thinking token 计入
output token。默认已经是 `low`;正确的做法是按调用抬高,而不是按 bot 抬高:

```python
bot.verify(page, "每一行都应用了折扣价", thinking_level="high")
```

**5. 放宽 `wait_for` 的轮询间隔。**每次轮询都是一次完整的 verify 调用。
默认 `interval=2.0` 配 `timeout=30.0` 最多是 15 次计费调用。预期页面本来
就慢时,`interval=5.0` 能砍掉一半以上。

**6. 把 `max_steps` 当止损,而不是调优旋钮。**它限制的是跑偏运行的损失
上限,并不会让一次成功的运行变便宜。出现 `max_steps` 结果说明预算给小了,
见[错误处理](/zh/advanced/error-handling)。

## 关于 prompt 缓存

引擎在排布 prompt 时,让可缓存前缀(system prompt + 工具 schema + 文本
历史)在各步之间保持字节稳定,历史窗口也改为批量截断,使该前缀每隔若干步
才断一次。

但这能否换来折扣是 Google 说了算:引擎不使用显式的 `cachedContent` API,
所以完全依赖 Gemini 的隐式缓存——而我们自己的实测中它经常不命中。把
`cache_read_tokens` 当作出现时的额外收益,而不是可以纳入预算的项。
`cache_write_tokens` 恒为 `0`。

## 度量真实花费

```python
bot = Qirabot()
...
u = bot.usage                      # 不可变快照;要最新数值就再读一次
print(u.ai_steps, u.input_tokens, u.output_tokens, u.total_tokens)
```

`bot.usage` 覆盖该客户端上的每一次 AI 调用。单次调用的数值在各结果对象上
(`ExtractResult`、`VerifyResult`、`LocateResult`),以及 `ai()` 的每个
`StepResult` 上。一次调用的花费是 `input_tokens + output_tokens`——
`output_tokens` 已经包含 thinking,不要再把 `thinking_tokens` 加一遍。
逐字段说明见[方法参考](/zh/reference/methods#usage)。

同一份汇总会在每次 CLI 任务结束后打印、出现在 HTML 报告头部,也在
`--output-format json` 的 `usage` 对象里。调优时想看逐次调用的明细,用
`QIRA_ENGINE_TRACE=<dir>` 可为每次模型调用写一条 JSONL 记录。

另见:[配置](/zh/advanced/configuration)(全部配置项及其环境变量) ·
[AI 任务与自定义工具](/zh/advanced/ai-tasks)
