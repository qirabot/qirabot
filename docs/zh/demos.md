---
title: 实战演示——真实、未剪辑的运行记录
description: 观看 Qirabot 在真机与真实浏览器中的实际运行：iOS/Android 游戏、移动端浏览器、Chrome。全部为未剪辑录屏，AI 全程只看屏幕画面，无 DOM、无选择器。
---

# 实战演示

下面每一段录屏都是真机或真实浏览器上的一次未剪辑运行。没有选择器、没有脚本
规则、不读取 DOM——模型看到的就是你能看到的截图，由它判断下一步动作，再由
Qirabot 执行。

视频按需加载，不点播放不会下载。

## iOS

### 《梦幻西游》手游：从创号自动玩到 15 级 {#mhxy_zero_to_15}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/mhxy_zero_to_15.poster.webp" src="https://assets.qirabot.com/demos/mhxy_zero_to_15.mp4"></video>

在 iPhone 真机上创建角色并自主完成新手流程——对话、任务、战斗、升级全程无人工
干预，200 步内练到 15 级（步数上限所致，并非能力上限）。录屏为 3 倍速。

脚本：[examples/game/ios_appium_mmorpg.py](https://github.com/qirabot/qirabot-python/blob/main/examples/game/ios_appium_mmorpg.py)

### 《剑与远征：启程》创号通关新手教程，进入大世界 {#afk_journey_tutorial}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/afk_journey_tutorial.poster.webp" src="https://assets.qirabot.com/demos/afk_journey_tutorial.mp4"></video>

在 iPhone 真机上创建角色、自主完成新手教程并进入大世界推进主线——对话、抽卡、
倍速自动战斗、自动寻路全程 AI 决策，战斗失败会先给英雄升级再重试，全程无人工
干预。录屏为 3 倍速。

### 通关 iOS Royal Match 益智消除 {#royal_match_puzzle}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/royal_match_puzzle.poster.webp" src="https://assets.qirabot.com/demos/royal_match_puzzle.mp4"></video>

在 iOS Royal Match 中识别棋盘上的图案，按颜色与形状判断可消组合，拖动交换相邻
图案凑齐三连消除，逐步推理通关 Level 2。

## Android

### 自主通关水果连连消手游 {#tile_match_game}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/tile_match_game.poster.webp" src="https://assets.qirabot.com/demos/tile_match_game.mp4"></video>

识别棋盘上的水果方块，点选凑齐三个相同的进行消除，一步步推理清空棋盘、通关
关卡。

### 巡检游戏大厅的非玩法 UI 功能 {#game_lobby_ui_check}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/game_lobby_ui_check.poster.webp" src="https://assets.qirabot.com/demos/game_lobby_ui_check.mp4"></video>

进入游戏大厅，逐项巡检各项非玩法 UI 功能，验证交互与显示是否正常。

### 在 lichess.org 上对弈国际象棋 {#lichess_play_chess}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/lichess_play_chess.poster.webp" src="https://assets.qirabot.com/demos/lichess_play_chess.mp4"></video>

在 Android 浏览器中打开 lichess.org，识别棋盘局面，推理并走子下国际象棋。

## Chrome

### 浏览 GitHub Trending 并提取热门仓库 {#github_trending_repo}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/github_trending_repo.poster.webp" src="https://assets.qirabot.com/demos/github_trending_repo.mp4"></video>

打开 GitHub Trending 页面，识别当日热门仓库并提取仓库信息。

### 在 Reddit 上回答 Python 新手提问 {#reddit_python_comment}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/reddit_python_comment.poster.webp" src="https://assets.qirabot.com/demos/reddit_python_comment.mp4"></video>

浏览 r/learnpython，找到并打开一条新手 Python 提问帖，读懂问题后点赞，再撰写
并提交一条切中要点的解答评论。

### 为指定 B站 UP 主的视频点赞并评论 {#bilibili_like_comment}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/bilibili_like_comment.poster.webp" src="https://assets.qirabot.com/demos/bilibili_like_comment.mp4"></video>

在 Chrome 中找到 B站 UP 主「xiaoy 解说」最新发布的第二个视频，等待播放与页面
加载稳定后点赞并撰写评论；若已点赞则跳过。

## 自己跑一个

- [快速开始](/zh/guide/quickstart)——两条命令跑通第一个任务
- [示例代码](https://github.com/qirabot/qirabot-python/tree/main/examples)——
  上述演示背后的脚本，以及 pytest 与框架接入示例
- [Android](/zh/backends/android) · [iOS](/zh/backends/ios) ·
  [浏览器](/zh/backends/browser)——各平台设备准备
