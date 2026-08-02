# qirabot one-line installer (Windows PowerShell).
#
#   powershell -ExecutionPolicy ByPass -c "irm https://qirabot.com/install.ps1 | iex"
#
# What it does — and nothing else:
#   1. Installs uv (https://docs.astral.sh/uv/) if missing. uv downloads a
#      Python on demand, so the machine needs no pre-installed Python.
#   2. `uv tool install "qirabot[browser]"` — an isolated environment; never
#      touches system Python or any existing virtualenv.
#   3. `qirabot install-browser` — one-time Chromium download for the browser
#      backend. (The Android / iOS / Windows-window backends need no extras.)
#
# Uninstall just as cleanly:  uv tool uninstall qirabot

$ErrorActionPreference = "Stop"

function Say($msg) { Write-Host "qirabot installer: $msg" -ForegroundColor Cyan }

# --- 1. uv -------------------------------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Say "uv not found - installing it first (from astral.sh)"
    Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
    # uv's installer targets %USERPROFILE%\.local\bin; make it visible to the
    # rest of THIS script even before a new shell picks up the PATH change.
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

# --- 2. qirabot (isolated tool environment) -----------------------------------
Say 'installing qirabot: uv tool install "qirabot[browser]"'
uv tool install --upgrade "qirabot[browser]"
# uv prints "already in PATH" to stderr; under EAP=Stop, Windows PowerShell 5.1
# turns redirected native stderr into a terminating NativeCommandError — relax
# EAP around the call so an informational line can't abort the install.
$eap = $ErrorActionPreference; $ErrorActionPreference = "Continue"
uv tool update-shell 2>&1 | Out-Null
$ErrorActionPreference = $eap

$qirabot = Join-Path "$env:USERPROFILE\.local\bin" "qirabot.exe"
if (-not (Test-Path $qirabot)) { $qirabot = "qirabot" }

# --- 3. Chromium (one-time, ~150 MB) ------------------------------------------
Say "downloading Chromium for the browser backend (one-time)"
& $qirabot install-browser

Say "done. Next steps:"
Write-Host ""
Write-Host '    gcloud auth application-default login   # Google Cloud credentials (or set GOOGLE_APPLICATION_CREDENTIALS)'
Write-Host '    qirabot browser "Search for SpaceX and get the first sentence of the article" --url wikipedia.org'
Write-Host ""
Write-Host "If 'qirabot' is not found, open a new terminal (PATH was just updated)."
