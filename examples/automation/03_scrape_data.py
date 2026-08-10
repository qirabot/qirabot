"""Standalone automation: scrape a few pages into structured JSON.

A realistic non-test use case: loop over URLs, extract a field from each with
AI, and save the results. No selectors, no per-site parsing code.

Install:
    uv pip install "qirabot[browser]"
    playwright install chromium

Run:
    gcloud auth application-default login   # auth: Google Cloud ADC, once
    python examples/automation/03_scrape_data.py
"""

import json

from qirabot import Qirabot

URLS = [
    "https://github.com/trending",
    "https://news.ycombinator.com",
    "https://www.wikipedia.org",
]

bot = Qirabot(task_name="scrape-data")
page = bot.open(headless=True)  # headless: no visible window for batch scraping

rows = []
for url in URLS:
    page.goto(url)
    bot.wait_for(page, "The page has finished loading", timeout=15.0)
    heading = bot.extract(page, "Get the main heading or site title")
    rows.append({"url": url, "heading": heading})
    print(f"{url} -> {heading}")

with open("scraped.json", "w", encoding="utf-8") as f:
    json.dump(rows, f, indent=2, ensure_ascii=False)
print("\nSaved scraped.json")

bot.close()
