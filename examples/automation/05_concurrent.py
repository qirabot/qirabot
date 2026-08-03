"""Standalone automation: run several browsers in parallel (process pool).

Each Qirabot drives one browser, so to work on N pages at once you run N
processes. The catch is the profile: a single on-disk profile can't be opened by
two Chrome instances at the same time. The fix is to keep one **logged-in master
profile** and hand each worker its own throwaway copy — every worker inherits the
login state but they never fight over the same directory.

Use threads instead of processes only if your work is I/O-bound and you don't
need separate profiles; for real browser fan-out, processes are the safe default.

Install:
    python -m pip install "qirabot[browser]"
    playwright install chromium

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/automation/05_concurrent.py
"""

import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

from qirabot import Qirabot

# One page per worker. Swap in your own targets (or rows from a CSV, etc.).
URLS = [
    "https://github.com/trending",
    "https://news.ycombinator.com",
    "https://www.wikipedia.org",
]

# A pre-logged-in profile to clone, or "" to launch each worker fresh.
# Point this at a directory you've already logged into (see the user_data_dir
# argument to bot.open in 01_quickstart.py) when the target needs a session.
BASE_PROFILE = ""


def process_url(index: int, url: str) -> str:
    """Runs in its own process: clone the profile, open the page, extract."""
    worker_profile = ""
    if BASE_PROFILE:
        # Each worker gets an independent copy that inherits the master's login
        # state but never collides with the others on disk.
        worker_profile = f"{BASE_PROFILE}_worker_{index}"
        shutil.rmtree(worker_profile, ignore_errors=True)
        shutil.copytree(
            BASE_PROFILE,
            worker_profile,
            # Skip lock/singleton files that would break the copy or the launch.
            ignore=shutil.ignore_patterns("Singleton*", "*.lock", "lockfile"),
        )

    bot = Qirabot(task_name=f"concurrent-{index}")
    try:
        page = bot.open(url, headless=True, user_data_dir=worker_profile)
        bot.wait_for(page, "The page has finished loading", timeout=15.0)
        heading = bot.extract(page, "Get the main heading or site title")
        print(f"[pid {os.getpid()}] {url} -> {heading}")
        return f"{url}: {heading}"
    finally:
        bot.close()
        if worker_profile:
            shutil.rmtree(worker_profile, ignore_errors=True)


def main() -> None:
    started = time.time()
    with ProcessPoolExecutor(max_workers=len(URLS)) as pool:
        futures = {pool.submit(process_url, i, url): url for i, url in enumerate(URLS)}
        for fut in as_completed(futures):
            url = futures[fut]
            try:
                print(fut.result())
            except Exception as e:
                print(f"{url} failed: {e}")
    print(f"\nDone in {time.time() - started:.1f}s")


# Required: ProcessPoolExecutor uses 'spawn' on macOS/Windows, which re-imports
# this module in each child. Without the guard the children would re-run main().
if __name__ == "__main__":
    main()
