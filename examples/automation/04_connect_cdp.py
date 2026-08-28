"""Standalone automation: drive a Chrome you already have open (CDP).

Instead of launching a fresh browser, connect to an existing Chrome over the
DevTools Protocol. Useful when you're already logged in, want to reuse your
profile/cookies, or are pointing at a remote browser (Browserless/Browserbase).

Start Chrome with remote debugging first:
    # macOS
    /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome \\
        --remote-debugging-port=9222
    # Linux
    google-chrome --remote-debugging-port=9222

Install:
    uv pip install "qirabot[browser]"

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/automation/04_connect_cdp.py
"""

from qirabot import Qirabot

bot = Qirabot(task_name="connect-cdp")

# Connects to the running Chrome and opens a fresh tab — your other tabs are
# left untouched. Don't pass headless/channel/args together with cdp_url:
# they can't apply to an already-running browser, so open() rejects the mix.
page = bot.open("https://www.wikipedia.org", cdp_url="http://localhost:9222")

title = bot.extract(page, "What is the main heading on this page?")
print(f"Heading: {title}")

bot.close()
