"""Sample job for the desktop runner.

Write and test this on your own machine, then POST it to the runner (see
trigger.sh, or `curl --data-binary @sample_job.py -H "X-Runner-Token: ..."
http://VM_HOST:8765/run`). It is a plain standalone Qirabot script — the
runner just does `python sample_job.py` inside the remote machine's logged-in
desktop session and streams stdout/stderr back to you live.

Printing the task_id (a local run id, `local-...`) plus the streamed step log
lets you follow the run without watching the screen; the full HTML report and
per-step screenshots land on the runner machine under `bot.report_dir`.
"""

import logging

import pyautogui
from qirabot import Qirabot

# The SDK logs each step ("step 3/10 <decision> -> click ...") at INFO level, but
# Python emits nothing until logging is configured. Turn it on so the runner can
# stream live progress back to you; without this you'd only see the prints below.
logging.basicConfig(level=logging.INFO, format="%(message)s")
# basicConfig turns on INFO for every library; quiet httpx so only the SDK's
# "step N/10 ..." lines show, not a "HTTP Request: ..." line per call.
logging.getLogger("httpx").setLevel(logging.WARNING)

bot = Qirabot(task_name="sample-browser-job")
print("task_id:", bot.task_id, flush=True)  # local run id — tells runs apart in the stream

# pyautogui can't launch apps, so open the target first. launch_app resolves the
# app by name cross-platform ("google chrome" here; e.g. "notepad" on Windows).
bot.launch_app("google chrome", wait=2)

result = bot.ai(
    pyautogui,
    "What are the top trending projects on GitHub today, output in Markdown format",
    max_steps=10,
)

print("success:", result.success)
print("output:", result.output)

bot.close()
