# Remote desktop runner — dedicated machine deployment (Windows / macOS)

Run your Qirabot desktop scripts on a **separate, dedicated machine** instead of
your own. You write and test a script locally; it runs on the remote machine's
desktop. Two problems this solves for desktop (pyautogui) automation:

- **Privacy/noise** — `pyautogui.screenshot()` captures the *whole* screen, so
  running it on your laptop sends your editor and other windows to the AI. On a
  dedicated VM the only thing on screen is the target app.
- **Mouse contention** — pyautogui drives the *real* cursor. On a dedicated VM it
  isn't fighting you for the mouse/keyboard.

This is a **deployment recipe + example runner**, not a built-in SDK feature. The
runner ([runner.py](runner.py)) is a tiny HTTP server: you `POST` a script to it
and it runs that script in the machine's logged-in desktop session, returning the
output.

```
your machine                                dedicated Windows VM (logged-in desktop)
────────────                                ────────────────────────────────────────
write & test myjob.py                       runner.py  (HTTP, port 8765)
        │  curl --data-binary @myjob.py             │
        ▼            POST /run                       ▼
  ───────────────────────────────────▶       python myjob.py  ──▶  Vertex AI (your GCP project)
  ◀──────── {returnCode, stdout, stderr} ──────────┘
```

> ⚠️ **Trust boundary.** `/run` executes whatever Python you POST — it is a
> remote-code-execution endpoint. That is fine on a machine **you** control, but
> keep the token set, bind it to a private network / VPN, and never run it on a
> shared/multi-tenant host or the public internet.

---

## 1. The one rule that trips everyone up

pyautogui needs a **real, logged-in graphical session** — it has to see the
screen and move the mouse. On Windows that means:

- **NOT** a Windows *Service* and **NOT** `session 0`. Services run in an
  isolated, headless session with no desktop — screenshots come back black and
  clicks go nowhere.
- **YES** the interactive desktop of a logged-in user — autostart the runner *as
  a logged-in user* via **Task Scheduler with "Run only when user is logged on"**,
  and keep that user logged in (auto-login below).

Steps 2–5 below are Windows; the same `runner.py` runs on **macOS** too — see
[On macOS](#on-macos) for the LaunchAgent + permissions equivalents. (Linux: a
systemd **user** service with `DISPLAY`/`WAYLAND_DISPLAY` set.)

## 2. Provision the VM

Any always-on Windows with a desktop (Hyper-V / VMware / a cloud Windows instance
/ a spare box):

```powershell
# Install Python 3.10+ (python.org or winget), then:
py -m pip install --upgrade pip
py -m pip install "qirabot[desktop]"
mkdir C:\qira            # copy runner.py here
```

## 3. Auto-login (so it boots into the interactive desktop)

Easiest is Sysinternals **Autologon** (`autologon.exe`), or set it manually as
admin (replace USER / PASS / COMPUTERNAME):

```powershell
$reg = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
Set-ItemProperty $reg AutoAdminLogon 1
Set-ItemProperty $reg DefaultUserName "USER"
Set-ItemProperty $reg DefaultPassword "PASS"
Set-ItemProperty $reg DefaultDomainName "COMPUTERNAME"

# keep the session interactive (no sleep / monitor-off):
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 0
```

> Auto-login stores credentials and drops the lock screen — only do it on a
> dedicated, access-controlled VM.

## 4. Set up auth / env vars and open the port

Auth is Google Cloud ADC. Either log in once **as the auto-login user** (`gcloud
auth application-default login`), or point at a service-account key file:

```powershell
[Environment]::SetEnvironmentVariable("GOOGLE_APPLICATION_CREDENTIALS", "C:\qira\sa.json", "User")
[Environment]::SetEnvironmentVariable("QIRA_MODEL", "gemini-vertex/gemini-3.6-flash", "User")  # optional
[Environment]::SetEnvironmentVariable("QIRA_RUNNER_TOKEN",  "some-shared-secret","User")
# allow the port through the firewall (restrict RemoteAddress to your subnet/VPN!)
New-NetFirewallRule -DisplayName "QiraRunner" -Direction Inbound -Action Allow `
  -Protocol TCP -LocalPort 8765 -RemoteAddress 10.0.0.0/8
```

Optional tuning (also user env vars):

| Variable | Default | Meaning |
|---|---|---|
| `QIRA_RUNNER_HOST` | `0.0.0.0` | bind address |
| `QIRA_RUNNER_PORT` | `8765` | listen port |
| `QIRA_RUNNER_TOKEN` | *(empty)* | required `X-Runner-Token`; empty = **unauthenticated** |
| `QIRA_RUNNER_JOB_TIMEOUT` | `600` | per-job wall-clock cap (seconds); `0` disables |

## 5. Autostart the runner in the user session

Task Scheduler job that runs **at logon** as the logged-in user (this is what
puts it on the interactive desktop):

```powershell
$action  = New-ScheduledTaskAction -Execute "py" -Argument "C:\qira\runner.py" -WorkingDirectory "C:\qira"
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName "QiraRunner" -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited
```

Reboot. Verify it's up from your machine:

```bash
curl http://VM_HOST:8765/health        # -> {"ok": true}
```

## 6. Trigger a job from your machine

Write/test a script locally (see [sample_job.py](sample_job.py)), then send it
with the [trigger.sh](trigger.sh) helper. The runner **streams the job's output
live** as it runs, so you see the task_id and each step as they happen — a long
AI run no longer looks frozen. trigger.sh exits with the job's return code
(handy in CI):

```bash
export QIRA_RUNNER_URL="http://VM_HOST:8765"
export QIRA_RUNNER_TOKEN="some-shared-secret"
./trigger.sh sample_job.py
```

Output streams line by line and ends with an exit marker:

```
task_id: local-1a2b3c4d
step 1/10: ... -> click
step 2/10: ... -> type_text
success: True
--- exit 0 ---
```

Or with plain curl (`-N` keeps it unbuffered so the stream shows live):

```bash
curl -sS -N --data-binary @sample_job.py \
     -H "X-Runner-Token: some-shared-secret" \
     http://VM_HOST:8765/run
```

The script runs on the VM's desktop. Pressing Ctrl+C on trigger.sh drops the
connection, which the runner detects and aborts the remote job.

## On macOS

The same `runner.py` works on a dedicated Mac (or macOS VM). Only the deployment
differs from Windows — these are the deltas to steps 2–5:

**Install** (steps 2):

```bash
python3 -m pip install "qirabot[desktop]"
mkdir -p ~/qira     # copy runner.py here
```

**Grant permissions (macOS-specific, the #1 gotcha).** pyautogui needs two grants
for the process that runs the script — without them screenshots are black and
clicks do nothing. In **System Settings → Privacy & Security**, add the Python
binary (the one `python3` resolves to) to:

- **Screen Recording** — for screenshots
- **Accessibility** — for mouse/keyboard control

(The grant is tied to that exact binary; if you later change Python versions,
re-grant. Run `which python3` to find it.)

**Auto-login** (step 3): System Settings → Users & Groups → Automatic login.
Then disable lock/sleep so the session stays interactive:

```bash
sudo pmset -a displaysleep 0 sleep 0          # don't sleep
# System Settings → Lock Screen: "require password" = Never, screensaver = Never
```

**Autostart in the GUI session** (step 5): use a **LaunchAgent** (per-user, runs
in your Aqua session) — *not* a LaunchDaemon. Create
`~/Library/LaunchAgents/com.qira.runner.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key>            <string>com.qira.runner</string>
  <key>ProgramArguments</key> <array>
    <string>/usr/bin/python3</string>
    <string>/Users/USER/qira/runner.py</string>
  </array>
  <key>EnvironmentVariables</key> <dict>
    <!-- Google Cloud ADC: omit if you ran `gcloud auth application-default login` as this user -->
    <key>GOOGLE_APPLICATION_CREDENTIALS</key> <string>/Users/USER/qira/sa.json</string>
    <key>QIRA_RUNNER_TOKEN</key><string>some-shared-secret</string>
  </dict>
  <key>RunAtLoad</key>        <true/>
  <key>KeepAlive</key>        <true/>   <!-- restart on crash -->
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.qira.runner.plist
curl http://localhost:8765/health     # -> {"ok": true}
```

The firewall step is the same idea: macOS will prompt to allow incoming
connections for Python the first time, or pre-allow it in System Settings →
Network → Firewall. Everything else (trigger.sh, debugging, the reliability
checklist) is identical.

## 7. Debugging without watching the screen

You can't see the remote desktop, so:

- **`task_id`** — have the script `print(bot.task_id)`; it's the local run id
  (`local-...`) and streams back early, so you can tell runs apart in the
  output and match them to reports on the VM.
- **live output** — the runner streams the script's stdout/stderr line by line as
  it runs; print whatever you need and you'll see it in real time.
- **`on_step`** — `bot.ai(..., on_step=lambda s: print(s.step, s.decision, s.action_type))`
  streams per-step progress into that output.
- **run report on disk** — on by default; `Qirabot(report_dir=...)` in your
  script controls where `report.html` + per-step screenshots land on the VM
  (`Qirabot(report=False)` to disable).

## 8. Reliability checklist

The runner is simple, but desktop automation has a few failure modes that look
like "it just stopped working." Tick these and it stays dependable:

- [ ] **Keep the session interactive.** Auto-login on (step 3) + screen-lock,
      sleep, and monitor-off all disabled. A locked/logged-out session is the #1
      cause of jobs silently failing — the runner is still up, but pyautogui can't
      see or touch the desktop. (Don't connect over RDP and then *disconnect* — that
      can lock the console; use the VM/hypervisor console instead.)
- [ ] **Autostart with restart-on-failure** (step 5). Verify it comes back after a
      reboot: `curl http://VM_HOST:8765/health`.
- [ ] **Keep a job timeout** (`QIRA_RUNNER_JOB_TIMEOUT`, default 600s). The server
      is single-threaded, so without it one hung script wedges the runner for good.
- [ ] **Monitor `/health`** from outside (a 1-line cron/uptime check). It's the
      fastest signal that the VM logged itself out or the runner died.
- [ ] **Pin the screen resolution.** The AI works in screenshot pixels; a VM that
      boots headless at a different resolution than you tested shifts every
      coordinate. Set a fixed resolution for the auto-login session.
- [ ] **Don't run two clients at once.** Input is serial; concurrent POSTs queue,
      and a slow first job can time out the second client.

## Files

- [runner.py](runner.py) — the HTTP runner (run this on the VM).
- [trigger.sh](trigger.sh) — send a script from your machine; prints output, exits with the job's code.
- [sample_job.py](sample_job.py) — a script to POST and test the pipeline end-to-end.
