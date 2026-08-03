#!/bin/sh
# qirabot one-line installer (macOS / Linux).
#
#   curl -LsSf https://qirabot.com/install | sh
#
# What it does — and nothing else:
#   1. Installs uv (https://docs.astral.sh/uv/) if missing. uv downloads a
#      Python on demand, so the machine needs no pre-installed Python.
#   2. `uv tool install "qirabot[browser]"` — an isolated environment; never
#      touches system Python or any existing virtualenv (no PEP 668 issues).
#   3. `qirabot install-browser` — one-time Chromium download for the browser
#      backend. (Android/iOS/Windows-window backends need no extras at all.)
#
# Uninstall just as cleanly:  uv tool uninstall qirabot

set -eu

say() { printf '\033[1;36mqirabot installer:\033[0m %s\n' "$1"; }

# --- 1. uv ------------------------------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    say "uv not found - installing it first (from astral.sh)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # uv's installer puts the binary in ~/.local/bin (XDG default); make it
    # visible to the rest of THIS script even if the user's PATH lacks it.
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

# --- 2. qirabot (isolated tool environment) ----------------------------------
say 'installing qirabot: uv tool install "qirabot[browser]"'
uv tool install --upgrade "qirabot[browser]"
# Ensure the tool bin dir is on PATH for future shells (no-op when it already is).
uv tool update-shell >/dev/null 2>&1 || true

QIRABOT="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")/qirabot"
[ -x "$QIRABOT" ] || QIRABOT=qirabot

# --- 3. Chromium (one-time, ~150 MB) -----------------------------------------
say "downloading Chromium for the browser backend (one-time)"
"$QIRABOT" install-browser

say "done. Next steps:"
printf '\n'
printf '    gcloud auth application-default login   # Google Cloud credentials (or set GOOGLE_APPLICATION_CREDENTIALS)\n'
printf '    qirabot browser "Search for SpaceX and get the first sentence of the article" --url wikipedia.org\n'
printf '\n'
printf 'If `qirabot` is not found, open a new terminal (PATH was just updated).\n'
