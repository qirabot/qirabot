# qirabot — Claude Code plugin

Drive any GUI with natural language — click, type, extract, and verify on web
browsers, Android, iOS, desktop apps, and games — using the
[Qirabot Python SDK](https://github.com/qirabot/qirabot) or the
`qirabot` CLI it ships with.

This directory is a self-contained Claude Code plugin. It bundles one Agent
Skill (`skills/qirabot/`) plus its preflight script, API reference, and starter
templates. The plugin ships only the *instructions and helper scripts*; the
`qirabot` Python package itself is installed at runtime by the skill's own
`scripts/preflight.py` (Claude Code plugins have no mechanism to declare pip
dependencies).

## Install

```text
/plugin marketplace add qirabot/claude-plugins
/plugin install qirabot@qirabot
```

After install the skill is available as `/qirabot:qirabot`, and Claude invokes it
automatically when a task involves automating, testing, or scraping a UI.

Not on Claude Code? The same skill ships inside the `qirabot` Python package —
`uv tool install qirabot && qirabot skill install agents` (or `codex` / `cursor` /
`--dir <path>`) copies it into any Agent-Skills-compatible tool's skills
directory, version-matched to the installed SDK. The
[skills CLI](https://github.com/vercel-labs/skills) works too:
`npx skills add qirabot/qirabot` (point it at the main repo — this
marketplace repo holds only the manifest, no skill files). The mirror lives at
`src/qirabot/skill-data/`, kept in sync by `scripts/sync_skill.py` and a CI
drift guard — edit the skill here under `skills/qirabot/`, then run the sync.

## Layout

```
plugins/qirabot/
├── .claude-plugin/
│   └── plugin.json          # plugin manifest (name, description, author)
└── skills/
    └── qirabot/
        ├── SKILL.md         # instructions Claude reads to operate the skill
        ├── references/
        │   ├── REFERENCE.md # condensed SDK API reference used at runtime
        │   └── CLI.md       # condensed qirabot CLI reference (one-shot runs)
        ├── scripts/
        │   └── preflight.py # environment checker — run before any script
        └── templates/
            ├── browser.py       # Playwright / web starter
            ├── android.py       # Android over adb starter (built in)
            ├── ios_wda.py       # iOS via WDA directly (built in, no Appium server)
            ├── ios_appium.py    # iOS via Appium XCUITest
            └── bolt_on.py       # bring-your-own-driver starter
```

## Releasing

The plugin is distributed via the lightweight `qirabot/claude-plugins`
marketplace, whose entry fetches this directory with a `git-subdir` source. By
default it tracks `main`, so merging here ships a new version; for a pinned
channel, point the marketplace's `source.ref` at a release tag (`vX.Y.Z`).
