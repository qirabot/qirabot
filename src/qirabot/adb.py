"""Zero-dependency Android device handle over the ``adb`` binary.

:class:`AdbDevice` is the SDK-level target for the direct Android backend:
``bot.ai(AdbDevice(), "...")``. It shells out to the platform-tools
``adb`` binary (pure stdlib — no airtest/uiautomator agent, nothing installed
on the device for screenshots/input), so the only host requirement is an adb
on PATH or a discoverable Android SDK.

Discovery, serial resolution and every subprocess call funnel through
:meth:`AdbDevice._run`, which remaps adb's stderr vocabulary (offline,
unauthorized, no devices, …) to actionable :class:`~qirabot.exceptions.QirabotError`s.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

from qirabot.exceptions import QirabotError

# Device states reported by `adb devices` that mean "present but unusable",
# each with the fix the user actually needs.
_BAD_STATE_HINTS = {
    "unauthorized": (
        "adb.unauthorized",
        "device {serial} is unauthorized — accept the USB-debugging prompt on "
        "the device screen (check 'Always allow'), then retry",
    ),
    "offline": (
        "adb.offline",
        "device {serial} is offline — reconnect the cable or run "
        "`adb disconnect {serial}` / `adb connect {serial}` for TCP devices",
    ),
}


def _which_adb() -> str | None:
    """Locate adb: $QIRA_ADB_PATH > PATH > $ANDROID_HOME|$ANDROID_SDK_ROOT."""
    override = os.environ.get("QIRA_ADB_PATH")
    if override:
        return override if os.path.isfile(override) else None
    found = shutil.which("adb")
    if found:
        return found
    exe = "adb.exe" if sys.platform == "win32" else "adb"
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        sdk = os.environ.get(env)
        if sdk:
            candidate = os.path.join(sdk, "platform-tools", exe)
            if os.path.isfile(candidate):
                return candidate
    return None


class AdbDevice:
    """Handle to one Android device driven directly over adb.

    Args:
        serial: adb serial from ``adb devices`` (e.g. ``emulator-5554`` or
            ``192.168.1.8:5555``). Optional when exactly one device is
            connected — resolved lazily on first use.
        adb_path: explicit path to the adb binary. Defaults to the discovery
            chain ``$QIRA_ADB_PATH`` → PATH → ``$ANDROID_HOME``/
            ``$ANDROID_SDK_ROOT`` platform-tools.
    """

    def __init__(self, serial: str | None = None, adb_path: str | None = None) -> None:
        self._serial = serial or None
        self._adb_path = adb_path or None

    def __repr__(self) -> str:
        return f"AdbDevice(serial={self._serial!r})"

    @property
    def adb_path(self) -> str:
        if self._adb_path is None:
            found = _which_adb()
            if not found:
                raise QirabotError(
                    "adb binary not found. Install Android platform-tools "
                    "(https://developer.android.com/tools/releases/platform-tools) "
                    "and put adb on PATH, or set ANDROID_HOME/QIRA_ADB_PATH.",
                    code="adb.not_found",
                )
            self._adb_path = found
        return self._adb_path

    @property
    def serial(self) -> str:
        """The resolved device serial (triggers device discovery on first use)."""
        if self._serial is None or not self._validated():
            self._resolve_serial()
        assert self._serial is not None
        return self._serial

    # A serial passed to __init__ is validated against `adb devices` exactly
    # once (on first use); after that it's trusted and _run's stderr remapping
    # catches later disconnects.
    _serial_checked = False

    def _validated(self) -> bool:
        return self._serial_checked

    @property
    def adb_command(self) -> list[str]:
        """Base command incl. device selection, e.g. for :class:`AdbScreenRecorder`."""
        return [self.adb_path, "-s", self.serial]

    def _devices(self) -> list[tuple[str, str]]:
        """Parse ``adb devices`` into ``[(serial, state), ...]``."""
        out = self._run(["devices"], scoped=False).stdout.decode("utf-8", "replace")
        rows: list[tuple[str, str]] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices"):
                continue
            parts = line.split()
            if len(parts) >= 2:
                rows.append((parts[0], parts[1]))
        return rows

    def _resolve_serial(self) -> None:
        rows = self._devices()
        if self._serial is not None:
            state = dict(rows).get(self._serial)
            if state is None:
                listing = ", ".join(s for s, _ in rows) or "none"
                raise QirabotError(
                    f"device {self._serial!r} not found (connected: {listing})",
                    code="adb.device_not_found",
                )
            self._check_state(self._serial, state)
            self._serial_checked = True
            return
        ready = [s for s, state in rows if state == "device"]
        if len(ready) == 1:
            self._serial = ready[0]
            self._serial_checked = True
            return
        if len(ready) > 1:
            raise QirabotError(
                "more than one device connected; pass serial= "
                f"(one of: {', '.join(ready)})",
                code="adb.multiple_devices",
            )
        # No usable device: explain the unusable ones, else "none at all".
        for serial, state in rows:
            self._check_state(serial, state)
        raise QirabotError(
            "no Android device found — plug one in with USB debugging enabled "
            "(or start an emulator) and check `adb devices`",
            code="adb.no_devices",
        )

    @staticmethod
    def _check_state(serial: str, state: str) -> None:
        if state == "device":
            return
        hint = _BAD_STATE_HINTS.get(state)
        if hint:
            code, msg = hint
            raise QirabotError(msg.format(serial=serial), code=code)
        raise QirabotError(
            f"device {serial} is in state {state!r} (expected 'device')",
            code="adb.bad_state",
        )

    def _run(
        self,
        args: list[str],
        *,
        scoped: bool = True,
        timeout: float = 30.0,
        check: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        """Single subprocess seam every adb call goes through.

        ``scoped=True`` prepends ``-s <serial>`` (resolving the serial first);
        stderr is rescanned on failure so a device that dropped offline
        mid-session surfaces as the same actionable error as at connect time.
        """
        cmd = [self.adb_path]
        if scoped:
            cmd += ["-s", self.serial]
        cmd += args
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except FileNotFoundError as e:
            self._adb_path = None  # stale cache (binary deleted); rediscover
            raise QirabotError(
                f"failed to execute adb at {cmd[0]!r}: {e}", code="adb.not_found"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise QirabotError(
                f"adb command timed out after {timeout:.0f}s: {' '.join(args)}",
                code="adb.timeout",
            ) from e
        if check and proc.returncode != 0:
            raise self._map_failure(args, proc)
        return proc

    def _map_failure(
        self, args: list[str], proc: subprocess.CompletedProcess[bytes]
    ) -> QirabotError:
        stderr = proc.stderr.decode("utf-8", "replace")
        blob = (stderr + "\n" + proc.stdout.decode("utf-8", "replace")).lower()
        serial = self._serial or "<device>"
        if "unauthorized" in blob:
            code, msg = _BAD_STATE_HINTS["unauthorized"]
            return QirabotError(msg.format(serial=serial), code=code)
        if "device offline" in blob or "device is offline" in blob:
            code, msg = _BAD_STATE_HINTS["offline"]
            return QirabotError(msg.format(serial=serial), code=code)
        if "not found" in blob and "device" in blob:
            return QirabotError(
                f"device {serial!r} not found — it disconnected; check `adb devices`",
                code="adb.device_not_found",
            )
        if "more than one device" in blob:
            return QirabotError(
                "more than one device connected; pass serial=",
                code="adb.multiple_devices",
            )
        if "no devices" in blob:
            return QirabotError(
                "no Android device found; check `adb devices`", code="adb.no_devices"
            )
        detail = stderr.strip() or proc.stdout.decode("utf-8", "replace").strip()
        return QirabotError(
            f"adb {' '.join(args)} failed (rc={proc.returncode}): {detail[:500]}",
            code="adb.command_failed",
        )

    # ---- conveniences the adapter/CLI build on -----------------------------

    def shell(self, command: str, *, timeout: float = 30.0) -> str:
        """Run a device shell command and return its decoded stdout."""
        proc = self._run(["shell", command], timeout=timeout)
        return proc.stdout.decode("utf-8", "replace")

    def screencap(self) -> bytes:
        """One PNG frame of the device screen (``exec-out screencap -p``)."""
        return self._run(["exec-out", "screencap", "-p"], timeout=60.0).stdout

    def install(self, apk_path: str, *, timeout: float = 120.0) -> None:
        """``adb install -r`` (replace-existing) an APK onto the device."""
        self._run(["install", "-r", apk_path], timeout=timeout)

    def wm_size(self) -> tuple[int, int]:
        """Current display size, preferring an active ``Override size``."""
        out = self.shell("wm size")
        override = re.search(r"Override size:\s*(\d+)x(\d+)", out)
        physical = re.search(r"Physical size:\s*(\d+)x(\d+)", out)
        m = override or physical
        if not m:
            raise QirabotError(
                f"could not parse `wm size` output: {out.strip()[:200]!r}",
                code="adb.wm_size",
            )
        return int(m.group(1)), int(m.group(2))
