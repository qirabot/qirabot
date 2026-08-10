---
title: 从 v2 升级——决策引擎本地化
description: Qirabot 的决策引擎从云端搬进 SDK 之后有哪些变化——用 Google Cloud 凭据替代 QIRA_API_KEY、如何选模型、已移除的 claude-vertex provider,以及如何锁定在 v2。
---

# 从 v2 升级

v3 把决策引擎从 Qirabot 云端搬进了 SDK 进程。截图现在直接从你的机器发往
你自己 Google Vertex AI 项目上的视觉模型。不再需要 Qirabot 账号、Qirabot
API key,链路里也不再有服务器。

**自动化 API 没有变化。**`bot.ai()`、`bot.click()` / `type_text()` /
`extract()` / `verify()` / `locate()`、`knowledge=`、`custom_tools=`、
`on_step=`、`thinking_level=`,以及全部后端(浏览器 / Android / iOS /
桌面 / Windows 窗口)、HTML 报告、录屏、悬浮窗,行为都和以前一样。变的
只是 SDK 如何认证、调用哪个模型。

## 你多半是因为这个报错找到这里的

```
Your Google Cloud setup works and Qirabot v3 is ready to use it — but a
v2-era QIRA_API_KEY is still set …
```

只要环境里还有 v2 的 `QIRA_API_KEY`,v3 就拒绝启动——而不是悄悄把账单
切换到某个 Google Cloud 项目上。清掉 v2 的变量,报错即消失:

```bash
unset QIRA_API_KEY QIRA_BASE_URL     # .env 和 CI secrets 里的也要一并删除
```

显式传 `model=`(或设置 `QIRA_MODEL`)同样算作确认切换,会解除这道守卫。

## 三步迁移

**1. 完成 Google Cloud 认证。**v3 使用标准的应用默认凭据(ADC):

```bash
gcloud auth application-default login
```

通过 `GOOGLE_APPLICATION_CREDENTIALS` 指向 service account JSON 同样可行,
在 GCE 上跑则直接用元数据服务器。如果你完全不想配 gcloud,还有两条基于
key 的路径:Vertex AI API key(`QIRA_VERTEX_API_KEY`),或给 `gemini`
provider 用的 AI Studio key(`QIRA_GEMINI_API_KEY`)。见
[配置](/zh/advanced/configuration)。

**2. 清掉 v2 的配置。**

| v2 | v3 |
|---|---|
| `QIRA_API_KEY` | 已移除——改用 Google Cloud ADC,或 Vertex / AI Studio API key |
| `QIRA_BASE_URL` | 已移除——不存在可指向的 Qirabot 服务器 |
| `Qirabot(model="fast")` 等云端别名 | `model="{provider}/{model}"`;别名概念已删除 |
| 服务端任务 id | `bot.task_id` 是本地 id,形如 `local-<8 位十六进制>` |

**3. 选模型(可选)。**不设置时,v3 使用
`gemini-vertex/gemini-3.6-flash`,项目取自你凭据自带的项目。用
`Qirabot(model=...)` 或 `QIRA_MODEL` 覆盖。

之后一次性检查整个环境:

```bash
qirabot doctor      # Python、ADC 与项目、后端依赖、残留的 v2 变量
qirabot models      # provider、默认模型、哪种认证可用
```

## provider 的变化

v3.0 提供了 `claude-vertex` 和 `gemini-vertex`。**`claude-vertex` 已在
v3.1 移除**——现在只有 `gemini-vertex` 和 `gemini` 两个 provider,传
`model="claude-vertex/..."` 会在构造时以"未知 provider"提示失败。仍然
需要 Vertex 上的 Claude,请锁定 `qirabot<3.1`。

## 计费与限流

模型调用由 Google Cloud 按该模型的价格在你的项目上计费;Qirabot 不再按步
收费。这对既有代码有两点影响:

- `InsufficientBalanceError`、`QirabotConnectionError`、
  `TaskTerminatedError` 和 `RateLimitError` 已在 **v3.2 移除**——已不存在
  Qirabot 的计费、连接和服务端任务状态,它们没有对应的东西可描述了
  (v3.0 和 v3.1 里它们还作为永不抛出的空壳导出)。请删掉针对它们写的
  `except` 子句,或改成捕获 `QirabotError`,见
  [错误处理](/zh/advanced/error-handling)。
- 配额现在是你项目的 Vertex AI 配额。限流在 provider 层内部重试,只有
  持续限流才会以 `ActionError` 暴露。控制账单见
  [控制成本](/zh/advanced/cost)。

## 继续留在 v2

v2 所对接的 Qirabot 云端后端已经关停,所以 v2 不是一条可持续的路——但
如果你需要缓冲时间,锁版本依然有效:

```bash
uv pip install "qirabot<3"
```

## 下一步

- [快速开始](/zh/guide/quickstart)——v3 的第一次运行
- [配置](/zh/advanced/configuration)——凭据、provider 与全部配置项
- [控制成本](/zh/advanced/cost)——一步花多少钱,哪些开关真正有效
