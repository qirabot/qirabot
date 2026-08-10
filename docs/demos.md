---
title: Demos — Real, Unedited Qirabot Runs
description: Watch Qirabot drive real apps on real devices — iOS and Android games, mobile browsers, and Chrome. Every run is unedited; the AI sees only pixels, no DOM and no selectors.
---

# Demos

Every clip below is a single unedited run on a real device or a real browser.
No selectors, no scripted rules, no DOM access — the model sees the same
screenshots you would, decides the next action, and Qirabot performs it.

Videos stream on demand; nothing loads until you press play.

## iOS

### Play an MMORPG from zero to level 15, hands-free {#mhxy_zero_to_15}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/mhxy_zero_to_15.poster.webp" src="https://assets.qirabot.com/demos/mhxy_zero_to_15.mp4"></video>

On a real iPhone, creates a character in Fantasy Westward Journey — a
top-grossing NetEase MMORPG — and completes the entire new-player flow:
dialogues, quests, battles, leveling to 15 with no human input. Capped at 200
steps, not by ability. Recording at 3× speed.

Script: [examples/game/ios_appium_mmorpg.py](https://github.com/qirabot/qirabot-python/blob/main/examples/game/ios_appium_mmorpg.py)

### Clear AFK Journey's tutorial and reach the open world {#afk_journey_tutorial}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/afk_journey_tutorial.poster.webp" src="https://assets.qirabot.com/demos/afk_journey_tutorial.mp4"></video>

On a real iPhone, creates a character in AFK Journey — Lilith's flagship RPG —
clears the guided tutorial, and pushes main-story quests in the open world:
dialogues, gacha pulls, speed auto-battles, auto-pathing, all AI-decided. After
a lost fight it levels its heroes and retries. Zero human input. Recording at
3× speed.

### Beat a Royal Match puzzle level on iOS {#royal_match_puzzle}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/royal_match_puzzle.poster.webp" src="https://assets.qirabot.com/demos/royal_match_puzzle.mp4"></video>

Reads the pieces on the Royal Match board, identifies matches by color and
shape, drags adjacent pieces to swap and form three-in-a-row clears, and
reasons step by step to clear Level 2.

## Android

### Beat a fruit tile-match game on its own {#tile_match_game}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/tile_match_game.poster.webp" src="https://assets.qirabot.com/demos/tile_match_game.mp4"></video>

Reads the fruit tiles on the board, taps and matches three of a kind to clear
them, and reasons step by step to empty the board and clear the level.

### Audit the non-gameplay UI in a game lobby {#game_lobby_ui_check}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/game_lobby_ui_check.poster.webp" src="https://assets.qirabot.com/demos/game_lobby_ui_check.mp4"></video>

Enters the game lobby and audits each non-gameplay UI feature, verifying that
interactions and visuals behave correctly.

### Play chess on lichess.org {#lichess_play_chess}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/lichess_play_chess.poster.webp" src="https://assets.qirabot.com/demos/lichess_play_chess.mp4"></video>

In the Android browser, opens lichess.org, reads the position on the board, and
reasons out its own moves to play a game of chess.

## Chrome

### Browse GitHub Trending & extract top repos {#github_trending_repo}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/github_trending_repo.poster.webp" src="https://assets.qirabot.com/demos/github_trending_repo.mp4"></video>

Opens GitHub Trending, identifies the day's hottest repositories, and extracts
their details.

### Answer a beginner Python question on Reddit {#reddit_python_comment}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/reddit_python_comment.poster.webp" src="https://assets.qirabot.com/demos/reddit_python_comment.mp4"></video>

Browses r/learnpython, finds and opens a beginner Python question, reads the
post, upvotes it, then writes and submits a helpful comment that directly
answers it.

### Like & comment on a Bilibili creator's video {#bilibili_like_comment}

<video controls preload="none" playsinline width="100%" poster="https://assets.qirabot.com/demos/bilibili_like_comment.poster.webp" src="https://assets.qirabot.com/demos/bilibili_like_comment.mp4"></video>

In Chrome, finds the second-most-recent video from Bilibili creator "xiaoy",
waits for playback and the page to settle, then likes it and writes a comment;
skips if already liked.

## Run something like this yourself

- [Quick Start](/guide/quickstart) — first task in two commands
- [Examples](https://github.com/qirabot/qirabot-python/tree/main/examples) —
  the scripts behind these runs, plus pytest and framework bolt-on samples
- [Android](/backends/android) · [iOS](/backends/ios) ·
  [Browser](/backends/browser) — device setup per platform
