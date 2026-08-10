---
title: 配合 AI Agent 使用——Agent Skill、Claude Code 插件与 Agent 友好的 CLI
description: 通过 AI agent 操作 Qirabot——符合开放标准的预置 Agent Skill(preflight 环境检查、API 参考、起步模板),支持 plugin marketplace、skills CLI 与内置的 qirabot skill install 三种安装方式。
---

# 配合 AI Agent 使用

Qirabot 既可以从代码调用,也可以交由 AI agent 操作。预置的 skill 符合
[Agent Skills 开放标准](https://agentskills.io),为 agent 提供 preflight
环境检查、精简版 SDK 与 CLI 参考,以及各平台起步模板。agent 接到自然语言
的自动化目标后,先校验环境,再选择执行路径(一次性任务直接调 CLI,流程
需要分支或消费返回值时编写 SDK 脚本),最后核对运行结果。安装方式取决于
所用的 agent。

## Claude Code 插件

[qirabot 插件](https://github.com/qirabot/claude-plugins)将该 skill 打包为
Claude Code 的 plugin marketplace 形式:

```text
/plugin marketplace add qirabot/claude-plugins
/plugin install qirabot@qirabot
```

凡是涉及自动化、测试或抓取 UI 的任务,Claude 会自动调用该 skill;也可以
显式调用 `/qirabot:qirabot`。skill 包含:

- **preflight 脚本**:在编写任何代码之前校验 Python 环境、后端依赖和
  Google Cloud 凭据(ADC),每一项失败的检查都附带确切的修复命令。
- **精简版 SDK + CLI 参考**:agent 依照经过防漂移校验的准确 API 面
  编写代码。
- **起步模板**:浏览器、Android(adb)、iOS(WDA 与 Appium)、自带
  driver 挂载各一份,agent 在可运行的骨架上修改,不用从零生成样板代码。

插件只包含指令和辅助脚本;`qirabot` 包本身由 preflight 在运行时引导安装。
marketplace 副本随仓库 `main` 分支自动更新;如需与本机 SDK 版本严格一致的
副本,改用 `qirabot skill install claude`。

## 任意其他 agent(Codex、Cursor、Copilot 等)

**安装 skill。** Agent Skills 格式已被 Codex、Cursor、Gemini CLI 等众多
工具支持;Claude Code 插件所含的同一份 skill 也打包在 `qirabot` 包内,先
[安装 qirabot](/zh/guide/installation):

```bash
qirabot skill install agents            # 共享的 .agents/skills 约定位置
qirabot skill install codex             # 或 claude、cursor
qirabot skill install --dir <path>      # 其他任何兼容 Agent Skills 的工具
```

此方式安装的副本与 SDK 版本严格一致:agent 读到的 API 参考始终描述它实际
运行的那个 `qirabot`。`--project` 装进仓库(`.agents/skills/`)而非家目
录;升级 qirabot 后重跑一次该命令即可。详见 [CLI 参考](/zh/guide/cli)。

也可以使用 [skills CLI](https://github.com/vercel-labs/skills),它从仓库
`main` 分支安装同一份 skill(指令最新,不与本机 SDK 版本绑定):

```bash
npx skills add qirabot/qirabot
```

即使不安装 skill,任意 agent 也可以靠下面两个特性直接操作 Qirabot:

**CLI 本身就是 agent 工具。** 一条 shell 命令就能执行完整的自然语言任务,
一次性工作不用生成代码:

```bash
qirabot browser "以 Jane Doe 填写注册表单,遇到验证码停下" --url example.com
```

退出码可机读(`0` 成功、`1` 失败、`130` 中断);每次运行都写出带逐步截图
的 [HTML 报告](/zh/advanced/reports),供 agent 或人工在失败时核查。全部
命令见 [CLI 参考](/zh/guide/cli)。

**文档对 agent 可读。** 在 agent 的上下文或 rules 文件中引用:

- `https://qirabot.com/docs/llms.txt`:索引 + 每页摘要
- `https://qirabot.com/docs/llms-full.txt`:全部文档合成一个文件
- 任何页面把 `.html` 换成 `.md` 即得纯 Markdown,例如
  `https://qirabot.com/docs/reference/methods.md`

Cursor 中可通过 `@Docs` 功能添加。

## 为什么 agent 适合视觉自动化

agent 生成 Playwright 代码时,必须猜测它观察不到的选择器,而这些选择器在
页面下一次改版时就会失效。用 Qirabot,agent 直接以自然语言描述元素,这也
正是它推理时所用的媒介。同一份 skill 还支持代码优先方案覆盖不到的场景:
原生移动 App、桌面软件、游戏。见 [Qirabot 是什么](/zh/)与
[平台支持矩阵](/zh/reference/api#平台支持矩阵)。
