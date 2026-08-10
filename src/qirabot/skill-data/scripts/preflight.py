#!/usr/bin/env python3
"""Qirabot skill preflight — verify the environment BEFORE writing an automation.

Uses only the standard library, so it runs on a stock Python *before* qirabot
is installed.

    python preflight.py [browser|android|ios|desktop]

Exit code 0 = ready to go; non-zero = fix the printed items first.
"""

import importlib
import os
import platform
import shutil
import subprocess
import sys

OK = "OK  "
NO = "FAIL"
WARN = "warn"


def line(status: str, label: str, hint: str = "") -> None:
    print(f"[{status}] {label}")
    if hint:
        print(f"       -> {hint}")


def load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from ./.env (stdlib only; mirrors qirabot.load_dotenv).

    Runs before qirabot is importable, so the credential/model checks below see
    QIRA_MODEL or GOOGLE_APPLICATION_CREDENTIALS values that live only in .env.
    A real exported env var still wins (setdefault).
    """
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        key, _, value = s.partition("=")
        key = key.removeprefix("export ").strip()
        if key:
            os.environ.setdefault(key, value.strip().strip('"').strip("'"))


def check_import(module: str, extra: str, label: str = "") -> bool:
    """Hard-check that an optional extra imports on THIS interpreter.

    Core `qirabot` does not pull in the backend extras (they're lazy), so a
    missing or broken one only surfaces mid-run. Importing here catches it —
    broadly, since a numpy/opencv ABI mismatch on an unsupported Python raises
    things other than ImportError. Prints an OK/FAIL line; returns True on success.
    """
    try:
        importlib.import_module(module)
        line(OK, label or f"{module} importable")
        return True
    except Exception as exc:  # noqa: BLE001
        line(NO, label or f"{module} importable",
             f'{type(exc).__name__}: {exc}  ->  uv pip install "qirabot[{extra}]"')
        return False


def main() -> int:
    target = (sys.argv[1] if len(sys.argv) > 1 else "browser").lower()
    if target not in {"browser", "android", "ios", "desktop"}:
        print(f"unknown target {target!r}; use browser | android | ios | desktop")
        return 2

    print(f"Qirabot preflight (target: {target})")
    # The whole point: this run validates ONE interpreter — echo which, so the
    # automation can be run with the exact same one (see the success message).
    print(f"interpreter: {sys.executable}\n")
    hard_ok = True
    # Use platform.system() (not sys.platform) so type-checkers don't statically
    # narrow the Windows-only branches to "unreachable" on a non-Windows host.
    is_windows = platform.system() == "Windows"

    # 1. Python version
    v = sys.version_info
    py_ok = (v.major, v.minor) >= (3, 10)
    line(OK if py_ok else NO, f"Python {v.major}.{v.minor} (need >= 3.10)",
         "" if py_ok else "Install Python 3.10+.")
    hard_ok = hard_ok and py_ok

    # 2. Google Cloud credentials (ADC) — the decision engine runs locally
    # against your own Vertex AI model, so ADC is the whole auth story. Also
    # accept GOOGLE_APPLICATION_CREDENTIALS from ./.env, like the templates'
    # load_dotenv().
    load_dotenv()
    gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if is_windows:
        adc_default = os.path.join(
            os.environ.get("APPDATA", ""), "gcloud", "application_default_credentials.json"
        )
    else:
        adc_default = os.path.expanduser(
            "~/.config/gcloud/application_default_credentials.json"
        )
    adc_hint = ("Run `gcloud auth application-default login`, or set "
                "GOOGLE_APPLICATION_CREDENTIALS to a service-account JSON "
                "(exported or in ./.env). On GCE/Cloud Run the metadata server "
                "also provides credentials — this check can't probe it.")
    if gac:
        if os.path.isfile(gac):
            line(OK, f"Google Cloud credentials (GOOGLE_APPLICATION_CREDENTIALS={gac})")
        else:
            line(NO, "GOOGLE_APPLICATION_CREDENTIALS points to a missing file",
                 f"{gac} does not exist — fix the path or unset it. {adc_hint}")
            hard_ok = False
    elif os.path.isfile(adc_default):
        line(OK, f"Google Cloud credentials (gcloud ADC at {adc_default})")
    else:
        line(NO, "Google Cloud credentials (ADC) not found", adc_hint)
        hard_ok = False

    # Model (informational — a missing QIRA_MODEL never fails preflight):
    # Qirabot() falls back to the built-in default.
    qira_model = os.environ.get("QIRA_MODEL", "").strip()
    if qira_model:
        line(OK, f"QIRA_MODEL={qira_model}")
    else:
        line(WARN, "QIRA_MODEL not set",
             "Default model gemini-vertex/gemini-3.6-flash will be used "
             "(override with QIRA_MODEL or Qirabot(model=...); list providers: qirabot models).")

    # 3. qirabot importable
    try:
        import qirabot  # noqa: F401
        line(OK, f"qirabot importable (v{getattr(qirabot, '__version__', '?')})")
    except ImportError:
        extra = {"browser": "[browser]", "android": "",
                 "ios": "", "desktop": "[desktop]"}[target]
        line(NO, "qirabot importable",
             f"uv pip install 'qirabot{extra}'  (prefer a venv: "
             "uv venv && source .venv/bin/activate)")
        hard_ok = False

    # 4. target-specific
    if target == "browser":
        # Core `qirabot` does NOT pull in Playwright, and a missing Chromium
        # binary only surfaces at bot.open() — verify both so "Ready" is real.
        if check_import("playwright", "browser", "playwright importable"):
            try:
                from playwright.sync_api import sync_playwright
                with sync_playwright() as p:
                    exe = p.chromium.executable_path
                if os.path.exists(exe):
                    # On Linux the download alone isn't enough — missing system
                    # libraries (e.g. libnspr4.so on a bare server) only surface
                    # at launch. ldd catches that here.
                    missing_libs = False
                    if sys.platform.startswith("linux"):
                        try:
                            ldd = subprocess.run(
                                ["ldd", exe], capture_output=True, text=True, timeout=10
                            )
                            missing_libs = "not found" in ldd.stdout
                        except Exception:  # noqa: BLE001
                            pass  # no ldd / probe failure: don't fail a browser that may work
                    if missing_libs:
                        line(NO, "Chromium system libraries",
                             "sudo playwright install-deps chromium")
                        hard_ok = False
                    else:
                        line(OK, "Chromium installed")
                else:
                    line(NO, "Chromium installed", "playwright install chromium")
                    hard_ok = False
            except Exception as exc:  # noqa: BLE001
                line(NO, "Chromium check failed", f"{exc}; try: playwright install chromium")
                hard_ok = False
        else:
            hard_ok = False
    elif target == "android":
        # The direct adb backend is built into core qirabot (pure stdlib); the
        # only thing that can be missing is the adb binary itself.
        adb = shutil.which("adb")
        line(OK if adb else NO, "adb on PATH",
             "" if adb else "Install Android platform-tools and connect a device/emulator.")
        hard_ok = hard_ok and bool(adb)
        if adb:
            try:
                out = subprocess.run([adb, "devices"], capture_output=True,
                                     text=True, timeout=10).stdout
                devs = [ln for ln in out.splitlines()[1:] if ln.strip() and "\tdevice" in ln]
                line(OK if devs else NO, f"Android device connected ({len(devs)})",
                     "" if devs else "Start an emulator or plug in a device (USB debugging on).")
                hard_ok = hard_ok and bool(devs)
            except Exception as exc:  # noqa: BLE001
                line(WARN, "adb devices", str(exc))
    elif target == "ios":
        # The direct WDA client is built into core qirabot — nothing to
        # install. Appium remains the alternative for simulators / auto WDA
        # builds; the WDA / device chain itself is external and can't be
        # checked from here.
        line(OK, "iOS WDA client built in (qirabot.WdaClient)")
        try:
            importlib.import_module("appium")
            line(OK, "appium client importable (optional, for simulators)")
        except Exception:  # noqa: BLE001
            line(WARN, "appium client not installed (optional)",
                 'uv pip install "qirabot[appium]"  (simulators / auto WDA build)')
        if shutil.which("appium"):
            line(OK, "appium server on PATH")
        else:
            line(WARN, "appium server not on PATH (Appium path)",
                 "npm i -g appium && appium driver install xcuitest  (skip if using a remote grid)")
        line(WARN, "iOS device + WebDriverAgent",
             "Needs macOS + Xcode, a built/signed WebDriverAgent, and a trusted device or simulator.")
    elif target == "desktop":
        # Desktop backends:
        #   * pyautogui — any OS (Win/macOS/Linux), drives the whole primary screen
        #   * qirabot.Window — built in, Windows-only, scopes to one window with
        #     game-readable scancode input (nothing to install).
        backends = []
        try:
            importlib.import_module("pyautogui")
            backends.append("pyautogui (whole screen, any OS)")
        except Exception:  # noqa: BLE001
            pass
        if is_windows:
            backends.append("qirabot.Window (built in, window-scoped)")
        if backends:
            line(OK, f"desktop backend importable ({'; '.join(backends)})")
        else:
            line(NO, "desktop backend importable",
                 'uv pip install "qirabot[desktop]"  (whole screen)')
            hard_ok = False
        line(WARN, "desktop runtime",
             "Ensure the target app is installed; on macOS grant Screen Recording + Accessibility.")

    # 5. ffmpeg (optional)
    if not shutil.which("ffmpeg"):
        line(WARN, "ffmpeg not found", "Optional — only needed for record=True screen recording.")
    elif is_windows:
        line(WARN, "ffmpeg ok",
             "record_audio=True (system sound) also needs a DirectShow loopback device — "
             "install screen-capture-recorder (virtual-audio-capturer) or enable 'Stereo Mix'.")

    print()
    if hard_ok:
        print("Ready. Copy a template from templates/, then run it with THIS exact")
        print("interpreter (NOT a bare `python`) so the run matches what was validated:")
        print(f"    {sys.executable} your_script.py")
        return 0
    print("Not ready — fix the FAIL items above, then re-run preflight.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
