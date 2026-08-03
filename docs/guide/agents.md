---
title: Use with AI Agents — Agent Skill, Claude Code Plugin & Agent-Friendly CLI
description: Operate Qirabot through an AI agent - a pre-built Agent Skill (open standard) with preflight checks, API references and starter templates, installable via plugin marketplace, the skills CLI, or the bundled qirabot skill install command.
---

# Use with AI Agents

Qirabot can be operated by an AI agent as well as called from code. A
pre-built skill following the
[Agent Skills open standard](https://agentskills.io) equips the agent with a
preflight environment check, condensed SDK and CLI references, and
per-platform starter templates. Given a natural-language automation goal, the
agent validates the environment, selects the execution path — the CLI for
one-shot tasks, an SDK script when the flow requires branching or returned
values — and verifies the run's outcome. Installation depends on the agent.

## Claude Code plugin

The [qirabot plugin](https://github.com/qirabot/claude-plugins) packages the
skill for Claude Code's plugin marketplace:

```text
/plugin marketplace add qirabot/claude-plugins
/plugin install qirabot@qirabot
```

Claude invokes the skill automatically whenever a task involves automating,
testing, or scraping a UI; it can also be invoked explicitly as
`/qirabot:qirabot`. The skill contains:

- **A preflight script** — validates the Python environment, backend
  dependencies, and Google Cloud credentials (ADC) before any code is
  written, and prints the exact fix command for each failing check.
- **Condensed SDK + CLI references** — the agent codes against an accurate,
  drift-tested API surface.
- **Starter templates** for browser, Android (adb), iOS (WDA and Appium),
  and bring-your-own-driver integration — the agent adapts a working
  skeleton instead of generating boilerplate from scratch.

The plugin contains instructions and helper scripts only; the `qirabot`
package itself is installed at runtime by the preflight bootstrap. The
marketplace copy auto-updates with the repository's `main` branch; for a copy
pinned to the installed SDK version, use `qirabot skill install claude`
instead.

## Any other agent (Codex, Cursor, Copilot, …)

**Installing the skill.** The Agent Skills format is supported by Codex,
Cursor, Gemini CLI, and many other tools. The same skill the Claude Code
plugin ships is bundled in the `qirabot` package:

```bash
pip install qirabot
qirabot skill install agents            # the shared .agents/skills convention
qirabot skill install codex             # or: claude, cursor
qirabot skill install --dir <path>      # any other Agent-Skills-compatible tool
```

The installed copy is version-matched to the SDK: the API reference the agent
reads always describes the `qirabot` it runs. `--project` installs into the
repository (`.agents/skills/`) instead of the home directory; rerun the
command after upgrading qirabot. Details: [CLI Reference](/guide/cli).

Alternatively, the [skills CLI](https://github.com/vercel-labs/skills)
installs the same skill from the repository's `main` branch (latest
instructions, not pinned to the installed SDK version):

```bash
npx skills add qirabot/qirabot
```

Independently of the skill, two properties make Qirabot straightforward for
agents to operate:

**The CLI functions as an agent tool.** A single shell command executes a
complete natural-language task; one-shot jobs require no code generation:

```bash
qirabot browser "Fill the signup form as Jane Doe and stop at the captcha" --url example.com
```

Exit codes are machine-checkable (`0` pass, `1` fail, `130` interrupted), and
every run writes an [HTML report](/advanced/reports) with per-step
screenshots for the agent or a human to inspect on failure. All commands:
[CLI Reference](/guide/cli).

**The documentation is agent-readable.** Reference these in the agent's
context or rules file:

- `https://qirabot.com/docs/llms.txt` — index with per-page summaries
- `https://qirabot.com/docs/llms-full.txt` — the complete documentation in
  one file
- every page as raw Markdown by substituting `.md` for `.html`, e.g.
  `https://qirabot.com/docs/reference/methods.md`

In Cursor, add them via the `@Docs` feature.

## Why agents and vision automation fit

An agent generating Playwright code must guess selectors it cannot observe,
and those selectors break on the next markup change. With Qirabot the agent
addresses elements in natural language — the modality it reasons in — and
the same skill extends to surfaces code-first stacks cannot reach: native
mobile apps, desktop software, and games. See [What is Qirabot](/) and the
[platform support matrix](/reference/api#platform-support-matrix).
