---
title: 安装
description: 用 uv 安装 Qirabot Python SDK 与 CLI——一行安装脚本或 uv tool install。包含各后端的 extras(browser/desktop/appium)与常见问题排查。
---

# 安装

下面这条命令会自动安装 [uv](https://docs.astral.sh/uv/)、qirabot 和
Chromium。qirabot 装在隔离环境里,不碰系统 Python;机器上也不需要预先装好
Python:

::: code-group

```bash [macOS / Linux]
curl -LsSf https://qirabot.com/install | sh
```

```powershell [Windows]
powershell -ExecutionPolicy ByPass -c "irm https://qirabot.com/install.ps1 | iex"
```

:::

如果机器上已经有 uv,手动执行等价的命令即可:

```bash
uv tool install "qirabot[browser]" && qirabot install-browser
```

如果要驱动的是设备而不是浏览器:Android(adb)、iOS(WDA)和 Windows
单窗口后端都内置在核心包里,安装只需:

```bash
uv tool install qirabot        # Android + iOS + Windows 窗口;零额外依赖
```

## 作为库使用

要在自己的测试里 `import qirabot`,应该把包装进项目环境而不是 tool 环境。
需要 Python 3.10+;机器上没有的话 `uv venv` 会自动下载一个:

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install "qirabot[browser]"
qirabot install-browser          # 或:playwright install chromium
```

## 各后端的 extras

核心包可以直接挂载到你已有的 Playwright / Selenium / Appium / pyautogui
会话上;框架依赖放在 extras 里。各平台页都写有该后端确切的安装命令:

| Extra | 后端 |
| --- | --- |
| `browser` | [Playwright——托管浏览器](/zh/backends/browser) |
| `desktop` | [pyautogui——全桌面,任意系统](/zh/backends/desktop) |
| `appium` | [Appium——经服务器驱动 Android/iOS;设备云](/zh/frameworks/appium) |
| `all` | 以上全部 |
| 无 | [Android](/zh/backends/android)(adb)、[iOS](/zh/backends/ios)(WDA)、[Windows](/zh/backends/windows-games) 单窗口和 [Selenium](/zh/frameworks/selenium)(自带 driver)都不需要 extra |

在 tool 环境里,要装的 extras 必须一次列全——
`uv tool install --force "qirabot[browser,desktop]"`。uv 会把环境替换成恰好
你请求的内容,单独补装 `[desktop]` 会把安装器原本装好的 `[browser]` 卸掉。
拿不准就跑 `qirabot doctor`,它会按你当前所处的环境打印正确的命令。

所有 extras 可以干净地装进同一个环境,2.0 起不再固定 numpy/opencv 版本。

## 检查环境

```bash
qirabot doctor
```

`doctor` 会报告 Python 版本、Google Cloud 凭据(ADC)能否解析及对应的
项目,以及各后端依赖,缺失项会附带确切的修复命令。如果还没有凭据,
运行[快速开始](/zh/guide/quickstart)的第一条命令
(`gcloud auth application-default login`)即可。

## 常见问题

- 一行安装脚本也可直接从 GitHub 仓库获取:
  `curl -LsSf https://raw.githubusercontent.com/qirabot/qirabot/main/scripts/install.sh | sh`
- 机器上没有 uv?用 `pip install "qirabot[browser]"` 装进已激活的 virtualenv
  效果一样。但装进系统 Python 不行:Debian/Ubuntu 按 PEP 668 会拦下来
  (`error: externally-managed-environment`)。
- 全新 Linux 机器要先执行一次 `sudo playwright install-deps chromium`,
  因为 Chromium 下载包不含其链接的系统库,否则会报
  `error while loading shared libraries: libnspr4.so ...`。

## 下一步

- [快速开始](/zh/guide/quickstart):完成 Google Cloud 认证,运行第一个任务
- [CLI 参考](/zh/guide/cli):不写代码,用一条命令运行自然语言任务
