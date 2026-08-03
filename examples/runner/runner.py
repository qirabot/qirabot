"""Example: a tiny HTTP runner — POST a script, it runs on this machine's desktop.

This is an EXAMPLE pattern, not a product. It lets you write and test a Qirabot
script on your own machine, then run it unchanged on a separate machine's desktop
(typically a Windows VM): screenshots only contain the target app — never your
editor — and the bot never steals your local mouse.

How it works:

    your machine                                this runner (remote desktop)
    ─────────────                               ────────────────────────────
    curl --data-binary @myjob.py  ───POST /run──▶  python myjob.py   (GUI session)
    ◀──────── live output stream ───────────────   stdout/stderr, line by line

The posted body is a normal standalone Qirabot script — it creates its own
``Qirabot(...)`` and passes ``pyautogui`` as the target (see sample_job.py). The
runner writes it to a temp file, runs it as a subprocess in the machine's
logged-in graphical session, and streams its merged stdout/stderr back live so
you see progress (and the task_id) as it runs. The stream ends with a
``--- exit N ---`` line carrying the script's return code.

Run it on the dedicated machine, inside a logged-in graphical session:

    python -m pip install "qirabot[desktop]"
    gcloud auth application-default login           # auth: Google Cloud ADC, once
    export QIRA_RUNNER_TOKEN="some-shared-secret"   # required unless you set it empty
    python runner.py

Trigger from your machine:

    curl -sS --data-binary @sample_job.py \
         -H "X-Runner-Token: some-shared-secret" \
         http://VM_HOST:8765/run

⚠️  TRUST BOUNDARY: /run executes whatever Python you POST. That is fine on a
machine YOU control, but it is a remote-code-execution endpoint — so:
  - keep QIRA_RUNNER_TOKEN set (requests without the right token are rejected),
  - bind it to a private network / VPN, not the public internet,
  - never run this on a shared/multi-tenant host.

LIMITATIONS (it's an example — adapt before relying on it):
  - Single-threaded: one job at a time (desktop input is serial anyway). A hung
    job blocks the next request until QIRA_RUNNER_JOB_TIMEOUT fires.
  - No TLS — put it behind a VPN/SSH tunnel, or front it with a reverse proxy.
  - No job history; capture the returned stdout on the client side.
  - Jobs fail silently if the desktop session is locked/logged out — see the
    reliability checklist in README.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = os.environ.get("QIRA_RUNNER_HOST", "0.0.0.0")
PORT = int(os.environ.get("QIRA_RUNNER_PORT", "8765"))
TOKEN = os.environ.get("QIRA_RUNNER_TOKEN", "")
# Per-job wall-clock cap. The server is single-threaded, so a hung script would
# otherwise wedge the runner forever and every later request would block. Default
# to 10 minutes; raise it for genuinely long jobs, or set 0 to disable.
JOB_TIMEOUT = float(os.environ.get("QIRA_RUNNER_JOB_TIMEOUT", "600")) or None
MAX_BODY = 5 * 1024 * 1024  # reject absurd uploads


class Handler(BaseHTTPRequestHandler):
    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/run":
            self._json(404, {"error": "not found"})
            return
        if TOKEN and self.headers.get("X-Runner-Token") != TOKEN:
            self._json(401, {"error": "bad or missing X-Runner-Token"})
            return

        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > MAX_BODY:
            self._json(400, {"error": "empty or oversized body"})
            return
        script = self.rfile.read(length)

        # Write to a temp .py and run it in this (GUI) session. The environment
        # is inherited, so Google Cloud ADC (GOOGLE_APPLICATION_CREDENTIALS),
        # QIRA_MODEL, and the display vars pass straight through.
        with tempfile.NamedTemporaryFile("wb", suffix=".py", delete=False) as f:
            f.write(script)
            path = f.name

        # Stream the job's output live (stdout+stderr merged) so the client sees
        # progress — and the task_id — as it happens, instead of waiting for the
        # whole run. The connection closes at EOF (HTTP/1.0), signalling the end
        # of the stream; a trailing "--- exit N ---" line carries the result.
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()

        proc = subprocess.Popen(
            [sys.executable, "-u", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=os.environ.copy(),
        )
        timed_out = False
        timer = None
        if JOB_TIMEOUT:
            def _kill() -> None:
                nonlocal timed_out
                timed_out = True
                proc.kill()
            timer = threading.Timer(JOB_TIMEOUT, _kill)
            timer.start()

        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    self.wfile.write(line)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    proc.kill()  # client (e.g. Ctrl+C in trigger.sh) left — abort the job
                    break
            rc = proc.wait()
        finally:
            if timer:
                timer.cancel()
            os.unlink(path)

        tail = f"\n--- exit {rc}{' (timed out)' if timed_out else ''} ---\n"
        try:
            self.wfile.write(tail.encode())
            self.wfile.flush()
        except OSError:
            pass

    def log_message(self, fmt: str, *args) -> None:
        # Concise access log to stdout.
        sys.stderr.write(f"{self.address_string()} {fmt % args}\n")


def main() -> int:
    # Auth is Google Cloud ADC; besides this env var it can also come from
    # `gcloud auth application-default login` or the GCE metadata server, so
    # an unset variable is only worth a note, not a hard failure.
    if not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        sys.stderr.write(
            "note: GOOGLE_APPLICATION_CREDENTIALS is not set — posted scripts "
            "will rely on gcloud / metadata-server ADC for Vertex AI auth\n"
        )
    if not TOKEN:
        sys.stderr.write("WARNING: QIRA_RUNNER_TOKEN is empty — /run is UNAUTHENTICATED\n")

    server = HTTPServer((HOST, PORT), Handler)  # single-threaded: jobs run serially
    sys.stderr.write(f"runner listening on http://{HOST}:{PORT}  (POST /run, GET /health)\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
