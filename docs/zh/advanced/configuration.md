---
title: 配置
description: Qirabot 的全部配置项——模型与 Vertex AI 设置、Google Cloud 凭据、构造函数参数、环境变量、思考深度与按调用覆盖、响应语言、settle 延迟调优。
---

# 配置

决策引擎在你自己的进程内运行,直接调用你在 Google Vertex AI 上的模型
端点。因此配置只有两件事:Google Cloud 凭据,以及用哪个模型。

```python
from qirabot import Qirabot

bot = Qirabot()  # model 参数 > QIRA_MODEL 环境变量 > gemini-vertex/gemini-3.6-flash
```

**凭据**使用标准的 Google Cloud Application Default Credentials(ADC):
把 `GOOGLE_APPLICATION_CREDENTIALS` 指向服务账号 JSON 文件,或执行一次
`gcloud auth application-default login`,或在 GCE 上由元数据服务器自动
提供。`qirabot doctor` 和 `qirabot models` 都会报告本机 ADC 是否可用。

配置也可以放项目 `.env`。脚本需显式启用:`from qirabot import
load_dotenv; load_dotenv()` 会读取 `$QIRA_DOTENV` 或 `./.env`,且从不
覆盖已导出的环境变量。CLI 自动加载 `.env`;SDK 自身从不读它。`.env` 里
典型的内容是 `QIRA_MODEL` 和 `QIRA_VERTEX_PROJECT`。

## 构造函数参数

| 参数 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `QIRA_MODEL` | `gemini-vertex/gemini-3.6-flash` | 模型,格式 `{provider}/{model}`([详情](#模型与语言)) |
| `vertex_project` | `QIRA_VERTEX_PROJECT` | 见下文 | Vertex 调用使用的 Google Cloud 项目 |
| `vertex_location` | `QIRA_VERTEX_LOCATION` | `"global"` | Vertex location/区域 |
| `vertex_api_key` | `QIRA_VERTEX_API_KEY` | `""` | 用 [Vertex AI API key](https://cloud.google.com/vertex-ai/generative-ai/docs/start/api-keys) 代替 ADC,无需配置 gcloud。仅对 `gemini-vertex` 有效,固定走全局端点,并覆盖 `vertex_project`/`vertex_location`。它不是 AI Studio key;`GOOGLE_API_KEY` 被刻意不读取 |
| `gemini_api_key` | `QIRA_GEMINI_API_KEY`、`GEMINI_API_KEY` | `""` | `gemini` provider 使用的 [AI Studio API key](https://ai.google.dev/gemini-api/docs/api-key)(走 Gemini Developer API,不涉及 Google Cloud) |
| `service_tier` | `QIRA_SERVICE_TIER` | `"standard"` | 计费档位:`standard` / `flex` / `priority`([详见](#计费档位)) |
| `tier_escalation` | `QIRA_TIER_ESCALATION` | `False` | 档位容量耗尽时向上升一档重试([详见](#计费档位)) |
| `thinking_level` | — | `"low"` | 所有操作的思考深度:`minimal` / `low` / `medium` / `high`([详情](#思考深度)) |
| `media_resolution` | `QIRA_MEDIA_RESOLUTION` | `"high"` | 模型看到的截图精细度:`low` / `medium` / `high` / `ultra_high`(仅 Gemini);调低可减少每步的图像 token |
| `language` | — | 跟随指令语言 | 响应语言:语言标签(`"zh"`、`"ja"`、`"de"` 等)或任意语言名称 |
| `task_name` | — | `""` | 任务名(显示在 HTML 报告里) |
| `locate_format` | `QIRA_LOCATE_FORMAT` | `""` | 元素定位输出格式;`bbox_yx_1000` 切换为归一化 y/x 包围盒 |
| `report` | — | `True` | 关闭时写 HTML 运行报告 |
| `report_dir` | `QIRA_REPORT_DIR` | `./qira_runs/...` | 报告输出根目录 |
| `record` | `QIRA_RECORD` | `False` | 录屏(ffmpeg) |
| `record_fps` | — | `12` | 录制帧率 |
| `record_window` | `QIRA_RECORD_WINDOW` | `False` | Windows:只录被测窗口 |
| `record_audio` | `QIRA_RECORD_AUDIO` | `False` | Windows:采集系统声音 |
| `record_audio_offset` | `QIRA_AUDIO_OFFSET` | `None` | 音画同步偏移(秒) |
| `record_device` | `QIRA_RECORD_DEVICE` | `False` | 录设备屏幕(adb / Appium) |
| `record_mjpeg_url` | `QIRA_RECORD_MJPEG_URL` | `None` | 录 MJPEG 流(iOS WDA) |
| `screenshot_annotate` | — | `True` | 在点击/输入坐标画红十字线 |
| `screenshot_format` | — | `"jpeg"` | `"jpeg"` 或 `"png"` |
| `screenshot_quality` | — | `80` | JPEG 质量,1–100 |
| `retry` | — | `1` | 瞬时失败的每动作重试次数(也可按调用传:`bot.click(..., retry=3)`) |
| `retry_delay` | — | `1.0` | 重试间隔(秒) |
| `settle_seconds` | `QIRA_SETTLE_SECONDS` | 按平台 | 每个动作后等 UI 重绘的暂停 |
| `overlay` | — | `False` | 置顶进度悬浮窗 + ESC 中止开关([详情](/zh/advanced/overlay)) |

`record*` 各开关实际产出什么(格式、各平台机制、文件落在哪)见
[报告与录屏](/zh/advanced/reports)。

**项目与 location 的解析顺序。**Vertex 项目:`vertex_project=` 参数 >
`QIRA_VERTEX_PROJECT` > `GOOGLE_CLOUD_PROJECT` > ADC 凭据自带的项目 id。
location:`vertex_location=` 参数 > `QIRA_VERTEX_LOCATION` >
`GOOGLE_CLOUD_LOCATION` > `"global"`。CLI 以全局参数暴露同一对配置,
写在子命令之前:`qirabot --vertex-project my-proj --vertex-location
us-east5 browser "..."`。

只有环境变量、没有构造参数对应的覆盖项:

| 环境变量 | 说明 |
|---|---|
| `GOOGLE_APPLICATION_CREDENTIALS` | Google 标准变量:ADC 使用的服务账号 JSON 路径 |
| `QIRA_ADB_PATH` | Android 后端显式指定 adb 可执行文件 |
| `QIRA_SCREEN_INDEX` | 多显示器机器上录哪块屏 |
| `QIRA_AUDIO_DEVICE` | 录音的音频设备(Windows) |
| `QIRA_DOTENV` | `load_dotenv()` 读取的路径,替代 `./.env` |
| `QIRA_RECORD_WINDOW_NATIVE` | Windows:强制旧式 gdigrab 逐窗口采集,替代默认的桌面裁剪方案 |
| `QIRA_TEXT_FALLBACK` | Windows:设为 `unicode` 时非 ASCII 输入从剪贴板粘贴回退为 unicode 注入 |
| `QIRA_MODIFIER_LEAD` / `QIRA_MODIFIER_TAIL` | 修饰键按住点击前后的等待秒数(桌面 adapter) |
| `QIRA_OVERLAY_DEBUG` | 设为 `1` 放行悬浮窗辅助进程的 stderr,便于诊断 |
| `QIRA_ENGINE_TRACE` | 调试用:指定一个目录;每次模型调用追加一条 JSONL 记录,并把该步截图存入该目录 |

## 模型与语言

`model` 决定所有操作背后的模型,格式为
`"{provider}/{model}"`:

| Provider | 提供 | 认证 | 默认模型 |
|---|---|---|---|
| `gemini-vertex` | Vertex AI 上的 Google Gemini 模型 | ADC,或 Vertex AI API key(`vertex_api_key=` / `QIRA_VERTEX_API_KEY`) | `gemini-3.6-flash` |
| `gemini` | Gemini Developer API 上的 Google Gemini 模型 | AI Studio API key(`gemini_api_key=` / `QIRA_GEMINI_API_KEY` / `GEMINI_API_KEY`) | `gemini-3.6-flash` |

```python
bot = Qirabot(model="gemini-vertex/gemini-3.6-flash")
bot = Qirabot(model="gemini")  # 只写 provider → 该 provider 的默认模型
```

只写 provider 名会解析为该 provider 的默认模型。什么都不配置时,
SDK 使用 `gemini-vertex/gemini-3.6-flash`。`qirabot models` 列出
各 provider、各自的默认模型,以及所配置的认证能否解析。

Qirabot 不按步计费:模型调用直接从你的机器发往你自己的 Vertex AI
项目,由 Google Cloud 按该模型的价格计费。

**关注成本:**`extract()` / `verify()` 的结果和 `ai()` 的每个
`StepResult` 都带有 `input_tokens` / `output_tokens` 字段,一次调用的
花费就是两者之和。见[方法参考](/zh/reference/methods#结果对象)。这些
token 究竟花在哪里、本页哪些开关能真正影响它们,见
[控制成本](/zh/advanced/cost)。

## 调用失败时

有两层各自在做重试,容易混淆是因为它们用的是同一批词。
[方法参考](/zh/reference/methods)里的 `retry=` 和 `timeout=` 是**你的**
旋钮:`timeout=` 是轮询屏幕直到元素看起来出现,`retry=` 是重做整个动作。
而在它们下面,引擎自己发出的模型调用有一套你无法配置的固定预算。本节讲
的是下面这一层。

### 单次调用的预算

| 调用 | 预算 |
|---|---|
| `ai()` 的每步决策、`extract()`、`verify()` | 120s |
| `locate()` | 60s |
| 连接端点 | 5s |

`locate()` 是决策的一半,因为引擎会把 locate 重试一次 —— 两发 locate 应该
和一次决策差不多贵。连接单独算,是因为"能不能连上端点"和"模型想多久"
无关;连不上的主机应该在几秒内报出来,而不是占满整个预算。

### 哪些会重试

| 失败 | 行为 |
|---|---|
| 限流(429) | 退避 5s → 10s → 20s → 30s,共 5 次,约一分钟 |
| 拒绝(503)、连接或连接池失败 | 退避 1s → 2s,共 3 次 |
| 模型回得太慢(读超时、504) | **立即失败** |
| 请求错误、认证失败、找不到(400/401/403/404) | 立即失败 |
| 空响应或无法解析 | 带纠正提示重问一次 |

每个退避延迟都带 ±20% 抖动,这样共用同一份配额的并发运行不会同步重试、
再次撞在一起。

其中两条时间规则值得记住,它们能解释你在日志里看到的大部分现象。
**限流要等很久**,因为配额是滚动的每分钟窗口:等过去是免费的而且通常就
成功了,而一次运行如果在第一次碰到配额时就死掉,已积累的进度会全部作废。
**回得慢的调用永远不重试**,因为请求确实到达了模型 —— 再问一遍同样的
问题,要再花一整个预算换来同样的答案。这也正是读超时和连接超时的区别:
它们长得像,含义相反。

### 一次运行能跑多久

没有任何东西限制 `ai()` 的墙钟时长,`max_steps`(默认 20)才是上界,
所以请用它来估算:一步最多两次模型调用 —— 决策本身,加上响应不可用时
的一次重问。

## 计费档位

`service_tier` 决定你的请求在 Google 的容量里如何被调度,价格与延迟
朝相反方向移动:

| 档位 | 价格 | 延迟 | 可用性 |
|---|---|---|---|
| `flex` | 约为标准价的 50% | 排队,见下方警告 | 可被丢弃 —— 负载高时会被拒绝 |
| `standard`(默认) | 基准价 | 秒级 | 尽力而为 |
| `priority` | 比标准价高约 75%~100% | 秒级,排在标准流量之前 | 超出容量时降级为标准档 |

```python
bot = Qirabot(model="gemini-vertex/gemini-3.6-flash", service_tier="priority")
```

::: warning 用之前先自己测一下 flex 的代价
flex 的容量是排队供给的,而在 Vertex 上这个等待更像是每请求的固定
开销,而不是随响应长度成比例增长。这一点对这里很关键:decide 和
locate 的响应都很短,固定开销要全额承担、没有东西可以摊薄;而 `ai()`
每步一次调用,代价还要乘以步数。同样的请求在 Gemini Developer API 上
被调度得便宜得多。

这个延迟到底多大,取决于模型、端点负载和时段,所以请在你自己的流量上
实测,不要照搬别处的数字。[运行报告](/zh/advanced/reports)里的分步耗时
能让这个对比变成跑两次就出结果的实验。

经验法则:flex 适合一次性的、无人值守的、答案晚点到也无所谓的任务。
交互式自动化正是排队档位最伤的场景。
:::

**计费按实际服务的档位算,而不是按你请求的档位算。** 端点无法安排的
档位会以标准档提供服务,并按标准价计费。

### 档位没生效时怎么查

**降级不会产生任何错误。** 响应是一个普通的 `200`,没有 error 字段,
也没有说明原因的响应头;唯一的线索是"实际服务档位"字段,Qirabot 每次
调用都会检查它(Vertex 看 `usageMetadata.trafficType`,Gemini Developer
API 看 `x-gemini-service-tier` 响应头)。不一致时每个会话打印一次
warning:

```
gemini-vertex: requested the priority tier but the request was served as
standard — billed at standard rates, and the endpoint gives no reason.
Config seen: model=gemini-3.6-flash location=global. …
```

原因只有三种,而 warning 会把它实际用的模型和端点打出来,让你一眼排除
前两种:

1. **端点不对。** Vertex 的非标准档位只在 global 端点提供;用区域性
   端点时它会接受 header 然后忽略。Qirabot 在构造时就会拒绝区域性
   `vertex_location`,所以只要 bot 建起来了,就不是这个原因。
2. **模型不支持该档位。** 各档位的覆盖范围不同,而且会变,查 Google
   的列表:[Vertex](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/priority-paygo)
   或 [Gemini Developer API](https://ai.google.dev/gemini-api/docs/pricing)。
   两个档位在这里的失败方式不同:flex 遇到不支持的模型会直接返回 `400`
   并点名(`Flex API is not supported for model: …`),priority 则只是
   降级。所以只要 flex 有结果返回,就说明模型是支持的。
3. **权限或容量。** Vertex Priority PayGo 有组织级的 ramp limit,
   Gemini Developer API 则把 priority 限制在更高的付费层级。响应里没有
   任何字段会说明这一层 —— 去 Cloud Console 查配额,或者问账号负责人。

要把"账号没权限"和"自己配置有问题"分开,最快的办法是把 Qirabot 摘出去,
直接问端点:

```bash
curl -sS -X POST \
  "https://aiplatform.googleapis.com/v1/projects/PROJECT/locations/global\
/publishers/google/models/gemini-3.6-flash:generateContent" \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  -H "X-Vertex-AI-LLM-Shared-Request-Type: priority" \
  -d '{"contents":[{"role":"user","parts":[{"text":"hi"}]}]}' \
  | grep trafficType
```

返回 `ON_DEMAND_PRIORITY` 说明档位可用,问题出在 SDK 这边的配置;返回
`ON_DEMAND` 说明账号拿不到,改客户端没有任何用。把 header 换成 `flex`
就能同样验证 flex。

运行报告的头部会给档位单独一行,紧挨模型。两者不一致时会直接写清楚 ——
`priority — served as standard, and billed at that rate` —— 没配置档位则
不显示这一行。

降级**不是**失败,也不会触发 `tier_escalation`:那次调用成功了,只是
按标准价计费。升档响应的是容量**失败** —— 限流、拒绝,或 flex 请求
在预算耗尽时仍在排队。

### 容量耗尽时升档

设置 `tier_escalation=True` 后,容量耗尽的档位会向上升一档重试
—— `flex` → `standard` → `priority` —— 并**在本次会话剩余时间里
留在新档位**,而不是让整次运行失败:

```python
bot = Qirabot(service_tier="standard", tier_escalation=True)
```

"容量耗尽"指的是限流(429)、拒绝(503),以及**仅 flex 才有的**
"请求还在排队、预算就先到期了"。升档发生在该种失败的常规重试
([调用失败时](#调用失败时))跑完之后,所以多久交接取决于是哪一种:

| 失败方式 | 交接时机 |
|---|---|
| 限流 | 跑完整个配额窗口之后,大约一分钟 |
| 拒绝 | 几秒 |
| flex 仍在排队 | 该次调用超时的那一刻 |

升档永远是一次长 `ai()` 运行在丢掉全部已积累进度之前的**最后一招**,
不是遇到失败的第一反应 —— 免费的手段总是先用。限流之所以要等就是这个
道理:窗口是滚动的、坐等不花钱,而升档会抬高单价。但升档后的那次调用
**不会再等第二个窗口** —— 各档位常常共用同一份配额,再睡一分钟只是为
等不来的容量拖住整个运行。

换档位对整个 bot 生命周期生效,因为另一种做法是每一步都花钱重新发现
同一处拥塞。新建一个 `Qirabot` 会重新探测。

这也改变了一次 flex 尝试值得等多久。开启升档时它只是一次**可以随时
放弃的探测**,拿到的是很短的期限而不是放宽后的预算;关闭升档时它保留
放宽的预算,因为等待是仅剩的选择。但无论哪种,flex 的重试次数和其他
档位一样 —— 拒绝是立刻返回的,再试一次只花几秒,而升档是价格翻倍。
只有"探测把整个预算烧光"这一种失败会立刻交接,因为重复它才是唯一
昂贵的动作。

对拥塞档位的效果才是重点。一个 20 步任务,flex 端点在排队、standard
正常:

| | 墙钟 | 完成步数 |
|---|---|---|
| `tier_escalation=False` | 每步一整个超时 | 0 |
| `tier_escalation=True` | 一次探测,之后标准档速度 | 全部 |

默认关闭,因为升档可能抬高每 token 单价 —— 但下行风险被上面那条计费
规则限死了:升到 `priority` 只有在真的由 priority 容量提供服务时才会
多花钱。

## 思考深度

`thinking_level` 在同一个模型内伸缩推理深度,难的判断多想,简单的
目标少想:

| 取值 | 权衡 |
|---|---|
| `minimal` | 最快最省,适合目标明显、界面干净的场景 |
| `low` | 默认档,步进快,足够覆盖常规 UI 判断 |
| `medium` | 需要更多判断的场景 |
| `high` | 推理最深,延迟和思考 token 开销也最高 |

```python
bot = Qirabot(thinking_level="low")                       # 任务级默认
bot.verify(page, "每一行都应用了折扣价",
           thinking_level="high")                         # 难的断言 → 多想想
```

构造函数设任务级默认,每个动作方法都可按调用覆盖。思考越深消耗的思考
token 越多,所以控成本的模式是:默认低档,只给难的调用升档。

一点注意:实际粒度取决于底层模型;部分模型会合并或钳位相邻深度,应把
取值理解为意图,而非四个严格区分的深度保证。

`language` 设定 AI 响应(提取文本、推理)的语言。常见语言标签(`"zh"`、`"ja"`、
`"ko"`、`"de"`、`"fr"` 等)会映射为对应语言;其余值——少见的标签或直接写语言
名称——原样传给模型。不设置时,响应跟随指令本身的语言:

```python
bot = Qirabot(language="zh")
text = bot.extract(page, "提取主标题", language="zh")
```

## Settle 延迟

每个改变屏幕的动作后,adapter 会短暂停顿,等 UI 重绘后再截下一张图;
否则模型可能截到动画中间帧,误判动作没有生效。默认值按平台调好
(桌面/Android `1.0` 秒,Selenium/Appium/WDA `0.6` 秒;Playwright 依赖自身的
auto-waiting,不加延迟)。

```python
bot = Qirabot(settle_seconds=1.5)   # 卡顿的远程设备:等久一点
bot = Qirabot(settle_seconds=0.3)   # 流畅的本地应用:快一点
bot = Qirabot(settle_seconds=0)     # 关闭;改用 wait_for()
```

这是一刀切的固定延迟。要"等 X 出现",优先用自动等待的 `timeout=` /
`wait_for()` 轮询,条件一成立立即返回。

## 运行生命周期

每个 `Qirabot` 实例管理一次本地运行:构造时分配运行 id(8 位十六进制,
可通过 `bot.task_id` 读取),每次调用记录为一个步骤,
`close()` / 上下文管理器退出时写出 HTML 报告。忘了 `close()` 有
`atexit` 兜底。构造函数会校验模型配置并解析 Google Cloud 凭据,配置
有误在构造时就报错,而不是运行到一半才失败。要以失败或取消而非完成
结束运行,见
[API 参考](/zh/reference/api#任务生命周期)中的 `fail()` / `cancel()`。
