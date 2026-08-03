---
title: 安装
description: 安装 Qirabot Python SDK 与 CLI——一行安装脚本、uv 或 pip。包含各后端的 extras(browser/desktop/appium)与常见问题排查。
---

# 安装

一行命令——自动安装 [uv](https://docs.astral.sh/uv/)、qirabot(隔离环境,
不碰系统 Python)和 Chromium,无需预装 Python:

::: code-group

```bash [macOS / Linux]
curl -LsSf https://qirabot.com/install | sh
```

```powershell [Windows]
powershell -ExecutionPolicy ByPass -c "irm https://qirabot.com/install.ps1 | iex"
```

:::

已经有 uv?手动执行等价命令:

```bash
uv tool install "qirabot[browser]" && qirabot install-browser
```

**驱动设备而不是浏览器?** Android(adb)、iOS(WDA)和 Windows 单窗口
后端内置在核心包里,安装只需:

```bash
uv tool install qirabot        # Android + iOS + Windows 窗口;零额外依赖
```

## pip / virtualenv

需要 Python 3.10+。请使用 virtualenv——Debian/Ubuntu 按 PEP 668 禁止向系
统 Python 安装:

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install "qirabot[browser]"
qirabot install-browser          # 或:playwright install chromium
```

**作为库使用**(在你自己的测试里 `import qirabot`):安装到项目环境而非
tool 环境——`uv pip install "qirabot[browser]"` 或上面的 pip 命令。

## 各后端的 extras

核心包可以直接挂载到你已有的 Playwright / Selenium / Appium / pyautogui
会话上。框架依赖放在 extras 里——装你正在用的那个,或者环境里已有就什么都
不用装:

```bash
python -m pip install "qirabot[browser]"   # Playwright(托管浏览器)
python -m pip install "qirabot[desktop]"   # pyautogui(全桌面,任意系统)
python -m pip install "qirabot[appium]"    # Appium(经服务器驱动 Android/iOS;设备云)
python -m pip install "qirabot[all]"       # 以上全部

python -m pip install qirabot selenium     # Selenium 不是 extra——自带 driver 即可
```

所有 extras 可以干净地装进同一个环境——2.0 起不再固定 numpy/opencv 版本。

## 检查环境

```bash
qirabot doctor
```

`doctor` 会报告 Python 版本、Google Cloud 凭据(ADC)能否解析及对应的
项目,以及各后端依赖——缺失项附带确切的修复命令。还没有凭据?
[快速开始](/zh/guide/quickstart)的第一条命令
(`gcloud auth application-default login`)就是修复方式。

## 常见问题

- 一行安装脚本也可直接从 GitHub 仓库获取:
  `curl -LsSf https://raw.githubusercontent.com/qirabot/qirabot/main/scripts/install.sh | sh`
- `error: externally-managed-environment` —— 你在往系统 Python 安装
  (PEP 668);改用上面的 uv 方式,或创建/激活 virtualenv。
- 全新 **Linux** 机器:先执行一次 `sudo playwright install-deps chromium`
  ——Chromium 下载包不含其链接的系统库
  (`error while loading shared libraries: libnspr4.so ...`)。
- **无显示器**环境(headless 服务器 / VM,无 `DISPLAY`):无法打开可见浏览
  器窗口——`bot.open()` 和 CLI 会自动检测并切换 headless,并给出警告。

## 下一步

- [快速开始](/zh/guide/quickstart) —— 完成 Google Cloud 认证,运行第一个任务
- [CLI 参考](/zh/guide/cli) —— 不写代码,用一条命令运行自然语言任务
