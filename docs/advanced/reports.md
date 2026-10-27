---
title: HTML Run Reports & Screen Recording
description: Every Qirabot run writes a self-contained HTML report with per-step screenshots - plus ffmpeg screen recording, device-screen recording for Android and iOS, per-window capture and system audio on Windows.
---

# Reports & Recording

## HTML run reports

By default every run writes a self-contained HTML report (with per-step
screenshots) when the bot closes, including on error or Ctrl+C, so you
can see where it stopped. Building the report makes no model calls and no
network requests; it is assembled from data captured during the run.

```python
bot = Qirabot(task_name="checkout")            # default: ./qira_runs/<date>/<time-id>/
bot = Qirabot(report_dir="./artifacts")        # custom root (or QIRA_REPORT_DIR)
bot = Qirabot(report=False)                    # off entirely — CI / library use
```

Output layout per run:

```
qira_runs/2026-06-07/192335-3f9ab2c1/
  report.html          # self-contained: embedded thumbnails + outcome badge per ai() task
  screenshots/         # full-resolution frames (001_click.jpg, 002_type_text.jpg, ...)
  recording.mp4        # if recording was on — embedded in the report
```

### What the report shows

**Header.** The title is `task_name` when you set one, else the first line
of the only task's instruction, else "N tasks". A long or multi-line
instruction is clamped to two lines with a "Full instruction" toggle. Below
it: run time, platform and screen size, and the run ID. Then three panels:

- **Outcome**: green when every `ai()` task passed, red when any failed,
  amber when the only problem is truncation (`MAX STEPS` /
  `MAX DURATION`) or cancellation; plus the pass tally and step count.
- **Time**: total time in AI steps, split into model time, device time
  (screenshot, action, settle, custom tools) and the time lost to capacity
  (rate limits, outages, timeouts); the same numbers as `bot.usage`.
- **Model & tokens**: the model (and fallback), the tier (and escalation),
  and the token total. Click the total for a table by model × tier: AI
  calls, prompt, cached, response, thinking and total. Failed calls are
  included; when no response named a tier, the tier shows `—`. The table
  prints expanded.

**Task overview.** With two or more `ai()` tasks, one row per task: its
badge, instruction, step count, time and time breakdown. Click a row to jump
to the task.

**Steps.** One table per task (headed "Steps" for a single task, "Task N"
otherwise), with manual steps grouped where they ran. Each `ai()` task's
badge matches `result.status`; a failure reason shows above the table.
Columns: `# / Time / Action / Detail / Model / Duration / Waits /
Screenshot`. Model is muted for the run's primary model, and a tier pill
appears when a call was served at another tier. Duration carries a small
model / device / wait bar, and a step that waited on capacity has its waits
tagged and the row tinted. Manual (non-AI) steps show `—` in those three
columns.

`screenshot_annotate=True` (default) draws a red crosshair at the resolved
click/type coordinates. Click a thumbnail to page through every screenshot;
with a recording, the time column seeks the video. `bot.report("path.html")`
writes an extra copy on demand; `bot.screenshot(target)` grabs a one-off
frame.

## Host screen recording

```python
bot = Qirabot(record=True)          # or QIRA_RECORD=1
page = bot.open("https://example.com")
bot.ai(page, "do the thing")
bot.close()                         # stops recording, then writes the report
```

Or control it manually:

```python
bot.start_recording()               # idempotent; fps via record_fps
try:
    bot.ai(page, "do the thing")
finally:
    bot.stop_recording()            # one recording per run — restart overwrites
```

Needs the `ffmpeg` binary on PATH. macOS: grant "Screen Recording"
permission (or you get black frames), and pick a monitor with
`QIRA_SCREEN_INDEX` if you have several. Recording is best-effort: a missing
ffmpeg or denied permission logs a warning and does not fail the task (check
`recording.ffmpeg.log` in the run dir).

## Device screen recording (Android / iOS)

The default recorder captures the *host* screen, so a phone doesn't appear
on it. Two switches record the device's own screen (both used by the CLI's
`android` / `ios --record`):

```python
# Android (or any Appium driver): recorder resolved from the action target
bot = Qirabot(record_device=True)   # AdbDevice -> adb screenrecord; Appium -> session API
bot.ai(dev, "open settings")
bot.close()                         # pulls the video into report_dir/recording.mp4

# iOS via WDA (no Appium): record WDA's MJPEG stream (port 9100;
# USB real device: `iproxy 9100 9100`). Needs ffmpeg on the host.
bot = Qirabot(record_mjpeg_url="http://127.0.0.1:9100")
```

`adb screenrecord` segments beyond its 3-minute cap are merged with ffmpeg.
If you quit an Appium driver yourself, call `bot.stop_recording()` first,
because the video lives in the session.

The CLI's `--record` flag maps onto these switches per target; see the
[CLI Reference](/guide/cli). Every `record*` constructor knob and its env
var is listed in [Configuration](/advanced/configuration).

## Windows: per-window capture + system audio

```python
from qirabot import Qirabot, Window

window = Window(title_re="Notepad.*")
bot = Qirabot(record=True, record_window=True, record_audio=True)
bot.ai(window, "type a note")
bot.close()                         # recording.mp4 = just that window, with sound
```

- `record_window=True` records only the window under test (Windows window
  backend; falls back to full screen otherwise) by cropping a desktop grab to
  the window's rectangle, which captures games and other GPU-composited
  windows correctly. Keep the window visible and unmoved: whatever overlaps
  that rectangle is recorded, and a minimized window yields nothing. See
  [Windows & Games](/backends/windows-games#recording-the-window) for the
  `gdigrab` alternative.
- `record_audio=True` captures system audio via a DirectShow loopback
  device. Install
  [screen-capture-recorder](https://github.com/rdp/screen-capture-recorder-to-video-windows-free)
  or enable "Stereo Mix". The device is auto-detected; override it with a
  device name or `QIRA_AUDIO_DEVICE`, and nudge A/V sync with
  `record_audio_offset=-0.4`.
