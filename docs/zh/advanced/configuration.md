---
title: 配置
description: Qirabot 的全部配置项——模型与 Vertex AI 设置、Google Cloud 凭据、构造函数参数、环境变量、思考深度与按调用覆盖、响应语言、settle 延迟调优。
---

# 配置

决策引擎在 SDK 内本地运行,直接调用你自己在 Google Vertex AI 上的模型
端点。因此配置只有两件事:Google Cloud 凭据,以及用哪个模型。

```python
from qirabot import Qirabot

bot = Qirabot()  # model 参数 > QIRA_MODEL 环境变量 > gemini-vertex/gemini-3.6-flash
```

**凭据**使用标准的 Google Cloud Application Default Credentials(ADC):
把 `GOOGLE_APPLICATION_CREDENTIALS` 指向服务账号 JSON 文件,或执行一次
`gcloud auth application-default login`,或在 GCE 上由元数据服务器自动
提供。`qirabot doctor` 和 `qirabot models` 都会报告本机 ADC 是否可用。

配置也可以放项目 `.env`:脚本需显式启用——`from qirabot import
load_dotenv; load_dotenv()`——读取 `$QIRA_DOTENV` 或 `./.env`,且从不
覆盖已导出的环境变量。CLI 自动加载 `.env`;SDK 自身从不读它。`.env` 里
典型的内容是 `QIRA_MODEL` 和 `QIRA_VERTEX_PROJECT`。

## 构造函数参数

| 参数 | 环境变量 | 默认值 | 说明 |
|---|---|---|---|
| `model` | `QIRA_MODEL` | `gemini-vertex/gemini-3.6-flash` | 模型,格式 `{provider}/{model}`([详情](#模型与语言)) |
| `vertex_project` | `QIRA_VERTEX_PROJECT` | 见下文 | Vertex 调用使用的 Google Cloud 项目 |
| `vertex_location` | `QIRA_VERTEX_LOCATION` | `"global"` | Vertex location/区域 |
| `thinking_level` | — | `"low"` | 所有操作的思考深度:`minimal` / `low` / `medium` / `high`([详情](#思考深度)) |
| `media_resolution` | `QIRA_MEDIA_RESOLUTION` | `"high"` | 模型看到的截图精细度:`low` / `medium` / `high` / `ultra_high`(仅 Gemini);调低可减少每步的图像 token |
| `language` | — | 模型默认 | 响应语言,如 `"zh"` / `"en"` |
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

Qirabot 不按步计费——模型调用直接从你的机器发往你自己的 Vertex AI
项目,由 Google Cloud 按该模型的价格计费。

**关注成本:**`extract()` / `verify()` 的结果和 `ai()` 的每个
`StepResult` 都带有 `input_tokens` / `output_tokens` 字段——一次调用的
花费就是两者之和。见[方法参考](/zh/reference/methods#结果对象)。

## 思考深度

`thinking_level` 在同一个模型内伸缩推理深度——难的判断多想,简单的
目标少想:

| 取值 | 权衡 |
|---|---|
| `minimal` | 最快最省——目标明显、界面干净 |
| `low` | 默认档——步进快,足够覆盖常规 UI 判断 |
| `medium` | 需要更多判断的场景 |
| `high` | 推理最深——延迟和思考 token 开销也最高 |

```python
bot = Qirabot(thinking_level="low")                       # 任务级默认
bot.verify(page, "每一行都应用了折扣价",
           thinking_level="high")                         # 难的断言 → 多想想
```

构造函数设任务级默认,每个动作方法都可按调用覆盖。思考越深消耗的思考
token 越多,所以控成本的模式是:默认低档,只给难的调用升档。

一点注意:实际粒度取决于底层模型;部分模型会合并或钳位相邻深度,应把
取值理解为意图,而非四个严格区分的深度保证。

`language` 设定 AI 响应(提取文本、推理)的语言——短语言标签如 `"zh"` /
`"en"`:

```python
bot = Qirabot(language="zh")
text = bot.extract(page, "提取主标题", language="zh")
```

## Settle 延迟

每个改变屏幕的动作后,adapter 会短暂停顿,等 UI 重绘后再截下一张图——
否则模型可能截到动画中间帧,误判动作没有生效。默认值按平台调好
(桌面/Android `1.0` 秒,Selenium/Appium/WDA `0.6` 秒;Playwright 依赖自身的
auto-waiting,不加延迟)。

```python
bot = Qirabot(settle_seconds=1.5)   # 卡顿的远程设备:等久一点
bot = Qirabot(settle_seconds=0.3)   # 流畅的本地应用:快一点
bot = Qirabot(settle_seconds=0)     # 关闭;改用 wait_for()
```

这是一刀切的固定延迟。"等 X 出现"请优先用自动等待的 `timeout=` /
`wait_for()` 轮询——条件一成立立即返回。

## 运行生命周期

每个 `Qirabot` 实例管理一次本地运行:构造时分配运行 id(`local-` 加
8 位十六进制,可通过 `bot.task_id` 读取),每次调用记录为一个步骤,
`close()` / 上下文管理器退出时写出 HTML 报告。忘了 `close()` 有
`atexit` 兜底。构造函数会校验模型配置并解析 Google Cloud 凭据,配置
有误在构造时就报错,而不是运行到一半才失败。要以失败或取消而非完成
结束运行,见
[API 参考](/zh/reference/api#任务生命周期)中的 `fail()` / `cancel()`。
