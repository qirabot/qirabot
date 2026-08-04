"""Cross-platform ffmpeg full-screen recorder (best-effort).

Records the whole screen to ``report_dir/recording.mp4`` so the HTML report
embeds it automatically. ffmpeg is a system binary (not a pip dependency), so it
is located with :func:`shutil.which` rather than imported; when it is missing,
the platform is unsupported, or the OS denies screen-capture permission, the
recorder only warns and degrades — it never raises into the running task.

Per platform the screen input differs:

* macOS  → ``-f avfoundation`` (needs the "Screen Recording" permission granted
  to the terminal/IDE running the script, else it records a black screen)
* Windows → ``-f gdigrab`` (and, optionally, per-window capture via
  ``title=``/``hwnd=`` plus system audio via a DirectShow loopback device)
* Linux  → ``-f x11grab`` (needs an X display; Wayland without XWayland can't be
  captured this way)

On Windows the recorder can additionally capture a single window and the system
audio (``audio=``). There are two window-capture modes, both best-effort (a
missing audio device or an unmappable window degrades to full-screen / silent
capture with a warning):

* ``region=(x, y, w, h)`` — grab the desktop and crop to that rectangle. This
  captures the *composited* frame the user actually sees, so it works for
  GPU/DirectX (game) windows that the per-window path renders black. The caller
  supplies physical pixels (see :func:`window_region`); whatever overlaps the
  rectangle on screen is recorded, so the window must be visible/foreground.
* ``window=`` (title or hwnd) — ``gdigrab`` per-window capture. Works for normal
  GDI windows even when partly occluded/background, but yields black/frozen
  frames for a minimized or GPU-composited (game) window.

When both are given, ``region`` wins. Prefer ``region`` for games and ``window``
when you must follow a background/occluded non-GPU window.

Typical usage is via the SDK (``Qirabot(record=True)`` or
``bot.start_recording()``), but it can be used standalone::

    from qirabot.recording import ScreenRecorder

    with ScreenRecorder("out.mp4"):
        ...  # do work; recording is finalized on exit
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import IO, Any, Callable, Protocol

logger = logging.getLogger("qirabot")


class Recorder(Protocol):
    """Structural interface shared by every recorder here (host screen, MJPEG
    stream, Appium session, adb screenrecord): best-effort ``start()``,
    graceful ``stop()`` returning the saved path (or ``None``), and an
    ``active`` liveness flag. The client holds one ``Recorder`` slot and never
    cares which implementation fills it."""

    output: str

    @property
    def active(self) -> bool: ...

    def start(self) -> bool: ...

    def stop(self, timeout: float = 10.0) -> str | None: ...

# avfoundation screen index used when device probing fails (on this Mac
# ``Capture screen 0`` is index 1).
_DEFAULT_SCREEN_INDEX = "1"

# Preferred Windows DirectShow device names for capturing *system* (loopback)
# audio, in priority order. ffmpeg has no native WASAPI loopback on Windows, so
# system sound must come through a dshow source: a WASAPI-loopback virtual device
# (driver-free, from screen-capture-recorder) or the driver-dependent Stereo Mix.
_WIN_SYSTEM_AUDIO_HINTS = ("virtual-audio-capturer", "stereo mix", "立体声混音")


def _find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg")


# DwmGetWindowAttribute index for the rectangle the window *visually* occupies
# (excludes the invisible resize border that GetWindowRect includes on Win10/11).
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


def window_region(hwnd: int) -> tuple[int, int, int, int] | None:
    """Visible bounds of ``hwnd`` as ``(x, y, w, h)`` in **physical** pixels.

    For feeding :func:`record`/:class:`ScreenRecorder`'s ``region=`` so a desktop
    grab can be cropped to exactly the window. Two Win10/11 quirks are handled:

    * The result comes from ``DWMWA_EXTENDED_FRAME_BOUNDS`` rather than
      ``GetWindowRect`` so it excludes the ~7px invisible resize border (which
      would otherwise show as empty margins / an offset in the recording).
    * The query runs under :func:`qirabot.windows.dpi_awareness` (a temporary
      per-monitor DPI thread context) so the rect is in physical pixels
      regardless of the host process's DPI awareness. ``gdigrab`` works in
      physical pixels, so the two line up.

    Windows-only; returns ``None`` on any other platform or on failure. Width and
    height are rounded down to even numbers (libx264 yuv420p requires it).
    """
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    from qirabot.windows import dpi_awareness

    try:
        with dpi_awareness():
            user32 = ctypes.windll.user32
            rect = wintypes.RECT()
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd),
                ctypes.c_uint(_DWMWA_EXTENDED_FRAME_BOUNDS),
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if hr != 0:  # not S_OK (e.g. DWM disabled) — GetWindowRect fallback
                if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                    return None
            x, y = rect.left, rect.top
            w, h = rect.right - rect.left, rect.bottom - rect.top
            if w <= 0 or h <= 0:
                return None
            return x, y, w - (w % 2), h - (h % 2)
    except Exception:
        logger.debug("window_region(%r) failed", hwnd, exc_info=True)
        return None


def _detect_audio_device(ffmpeg: str) -> str | None:
    """Resolve a Windows DirectShow *system audio* device name (Windows only).

    ``ffmpeg -list_devices true -f dshow -i dummy`` writes lines like
    ``"virtual-audio-capturer" (audio)`` to stderr; we pick the first device
    whose name matches a known loopback/stereo-mix hint. ``QIRA_AUDIO_DEVICE``
    overrides the probe; probe failures (or no match) return ``None`` so the
    caller degrades to a silent recording.
    """
    override = os.environ.get("QIRA_AUDIO_DEVICE")
    if override:
        return override
    try:
        proc = subprocess.run(
            [ffmpeg, "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
            capture_output=True,
            # UTF-8 + replace, not the OS locale codec: a GBK decode of ffmpeg's
            # output crashes the stdio reader thread and leaves stderr=None.
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # Device names are quoted; the audio block lists them as `"name" (audio)`.
    names: list[str] = re.findall(r'"([^"]+)"', proc.stderr or "")
    lowered = [(n, n.lower()) for n in names]
    for hint in _WIN_SYSTEM_AUDIO_HINTS:
        for name, low in lowered:
            if hint in low:
                return name
    return None


def _detect_screen_index(ffmpeg: str) -> str:
    """Resolve the avfoundation screen device index (macOS only).

    ``ffmpeg -f avfoundation -list_devices true -i ""`` writes lines like
    ``[1] Capture screen 0`` to stderr; we take the ``[N]`` in front of the
    first "Capture screen" line. ``QIRA_SCREEN_INDEX`` overrides the probe;
    probe failures fall back to :data:`_DEFAULT_SCREEN_INDEX`.
    """
    override = os.environ.get("QIRA_SCREEN_INDEX")
    if override:
        return override
    try:
        proc = subprocess.run(
            [ffmpeg, "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            capture_output=True,
            # UTF-8 + replace, not the OS locale codec (see _detect_audio_device).
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return _DEFAULT_SCREEN_INDEX
    for line in (proc.stderr or "").splitlines():
        if "Capture screen" in line:
            m = re.search(r"\[(\d+)\]\s*Capture screen", line)
            if m:
                return m.group(1)
    return _DEFAULT_SCREEN_INDEX


def _win_video_target(window: str | None) -> str:
    """gdigrab input filename for ``window``.

    A numeric string is treated as a window handle (``hwnd=``, needs a recent
    ffmpeg); any other non-empty string is a window title (``title=``, widely
    supported). Empty/None records the whole desktop.
    """
    if not window:
        return "desktop"
    if window.isdigit():
        return f"hwnd={window}"
    return f"title={window}"


def _build_input_args(
    plat: str,
    fps: int,
    capture_cursor: bool,
    *,
    screen_index: str = _DEFAULT_SCREEN_INDEX,
    window: str | None = None,
    region: tuple[int, int, int, int] | None = None,
    audio_device: str | None = None,
    audio_offset: float | None = None,
) -> list[str] | None:
    """Build the ffmpeg screen-capture input args for ``plat``.

    Returns the args between ``ffmpeg -y`` and the output codec args, or
    ``None`` when the platform isn't supported. Pure (no subprocess / env reads
    beyond ``DISPLAY``) so it's unit-testable; ``screen_index`` is resolved by
    the caller for macOS.

    ``window``/``region``/``audio_device`` are Windows-only for now (per-window
    capture, desktop-crop capture, and system-audio loopback); other platforms
    ignore them and keep full-screen, silent capture. ``region`` (a physical-px
    ``(x, y, w, h)``) takes precedence over ``window``: it grabs the desktop and
    crops to the rectangle, which — unlike per-window capture — works for
    GPU/game windows.
    """
    if plat == "darwin":
        return [
            "-f", "avfoundation",
            "-framerate", str(fps),
            "-capture_cursor", "1" if capture_cursor else "0",
            "-i", f"{screen_index}:none",  # video index + no audio
        ]
    if plat == "win32":
        args = ["-f", "gdigrab", "-framerate", str(fps)]
        if not capture_cursor:
            args += ["-draw_mouse", "0"]  # gdigrab draws the cursor by default
        if region is not None:
            # Desktop grab cropped to the window rect. Captures the composited
            # frame (works for GPU windows); offsets/size are physical pixels.
            x, y, w, h = region
            args += [
                "-offset_x", str(x),
                "-offset_y", str(y),
                "-video_size", f"{w}x{h}",
                "-i", "desktop",
            ]
        else:
            args += ["-i", _win_video_target(window)]
        if audio_device:
            # Second input: system audio via DirectShow. rtbufsize/thread_queue
            # absorb loopback bursts; itsoffset (negative) compensates the
            # loopback delay so audio lines up with the video.
            if audio_offset is not None:
                args += ["-itsoffset", str(audio_offset)]
            args += [
                "-f", "dshow",
                "-thread_queue_size", "1024",
                "-rtbufsize", "100M",
                "-i", f"audio={audio_device}",
            ]
        return args
    if plat.startswith("linux"):
        display = os.environ.get("DISPLAY") or ":0.0"
        args = ["-f", "x11grab", "-framerate", str(fps)]
        if not capture_cursor:
            args += ["-draw_mouse", "0"]  # x11grab draws the cursor by default
        args += ["-i", display]
        return args
    return None


class ScreenRecorder:
    """Start/stop a single ffmpeg subprocess recording the full screen."""

    def __init__(
        self,
        output: str,
        *,
        fps: int = 12,
        capture_cursor: bool = True,
        window: str | None = None,
        region: tuple[int, int, int, int] | None = None,
        audio: bool | str = False,
        audio_offset: float | None = None,
    ):
        self.output = output
        self.fps = fps
        self.capture_cursor = capture_cursor
        # Windows-only extras. ``region``: physical-px (x, y, w, h) to crop a
        # desktop grab to (works for GPU/game windows; takes precedence over
        # ``window``). ``window``: a window title (or numeric handle) for
        # per-window capture instead of the whole desktop. ``audio``: True =
        # auto-detect a system-audio device, a str = explicit dshow device name,
        # False = no audio. ``audio_offset``: seconds (usually negative) to shift
        # audio for A/V sync. All degrade gracefully when unavailable.
        self.window = window
        self.region = region
        self.audio = audio
        self.audio_offset = audio_offset
        self._proc: subprocess.Popen[bytes] | None = None
        self._log: IO[bytes] | None = None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def _build_cmd(self, ffmpeg: str) -> tuple[list[str], str] | None:
        """Full ffmpeg command plus a human description for the start log line.

        Returns ``None`` (after warning) when recording isn't possible here —
        subclasses override this to record a different source with the same
        start/stop lifecycle.
        """
        index = _detect_screen_index(ffmpeg) if sys.platform == "darwin" else _DEFAULT_SCREEN_INDEX

        # Resolve system-audio device (Windows only). True -> probe; str -> use
        # as-is. A requested-but-missing device degrades to a silent recording.
        audio_device: str | None = None
        if self.audio and sys.platform == "win32":
            audio_device = self.audio if isinstance(self.audio, str) else _detect_audio_device(ffmpeg)
            if not audio_device:
                logger.warning(
                    "recording: no system-audio device found (install screen-capture-recorder's "
                    "'virtual-audio-capturer' or enable 'Stereo Mix', or set QIRA_AUDIO_DEVICE); "
                    "recording without sound"
                )
        elif self.audio and sys.platform != "win32":
            logger.warning("recording: audio capture is currently Windows-only; recording without sound")

        input_args = _build_input_args(
            sys.platform, self.fps, self.capture_cursor,
            screen_index=index,
            window=self.window,
            region=self.region,
            audio_device=audio_device,
            audio_offset=self.audio_offset,
        )
        if input_args is None:
            logger.warning("recording skipped: platform %r not supported", sys.platform)
            return None

        cmd = [
            ffmpeg, "-y",
            *input_args,
            "-vcodec", "libx264",
            "-preset", "ultrafast",   # minimize CPU stolen from the task being driven
            "-pix_fmt", "yuv420p",    # HTML5 / inline-playback compatible
        ]
        if audio_device:
            cmd += ["-c:a", "aac", "-b:a", "160k"]
        cmd.append(self.output)

        if self.region is not None:
            scope = f"region {self.region}"
        elif self.window:
            scope = f"window {self.window!r}"
        else:
            scope = "full screen"
        sound = f"audio={audio_device}" if audio_device else "no audio"
        return cmd, f"{self.fps}fps, {scope}, {sound}"

    def start(self) -> bool:
        """Start recording; return True on success.

        Best-effort: missing ffmpeg / unsupported platform only warn and return
        False — they never raise.
        """
        if self._proc is not None:
            return self.active

        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            logger.warning("recording skipped: ffmpeg not found (install it and ensure it's on PATH: https://ffmpeg.org/download.html)")
            return False

        built = self._build_cmd(ffmpeg)
        if built is None:
            return False
        cmd, describe = built

        os.makedirs(os.path.dirname(os.path.abspath(self.output)), exist_ok=True)
        log_path = os.path.join(os.path.dirname(self.output), "recording.ffmpeg.log")

        try:
            self._log = open(log_path, "wb")
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,    # used for graceful stop (write 'q')
                stdout=self._log,
                stderr=subprocess.STDOUT,
            )
        except OSError as e:
            logger.warning("recording failed to start: %s", e)
            self._cleanup_log()
            self._proc = None
            return False

        logger.info("recording started (%s) -> %s", describe, self.output)
        return True

    def stop(self, timeout: float = 10.0) -> str | None:
        """Gracefully stop and flush to disk; return the output path or None."""
        proc = self._proc
        if proc is None:
            return None
        self._proc = None

        if proc.poll() is None:
            try:
                # Send 'q' so ffmpeg exits normally and writes the mp4 moov atom,
                # keeping the file seekable/playable.
                if proc.stdin:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
                    proc.stdin.close()
            except (OSError, ValueError):
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

        self._cleanup_log()

        if os.path.exists(self.output) and os.path.getsize(self.output) > 0:
            logger.info("recording saved: %s", self.output)
            return self.output
        log_path = os.path.join(os.path.dirname(self.output), "recording.ffmpeg.log")
        logger.warning("recording produced no valid file (see %s)", log_path)
        return None

    def _cleanup_log(self) -> None:
        if self._log is not None:
            try:
                self._log.close()
            except OSError:
                pass
            self._log = None

    def __enter__(self) -> ScreenRecorder:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()


class MjpegStreamRecorder(ScreenRecorder):
    """Record an HTTP MJPEG stream (e.g. WebDriverAgent's screen stream) to mp4.

    The host-screen grabbers above can't see a phone's screen, but WDA serves
    the iOS device screen as an MJPEG stream on its ``mjpegServerPort``
    (default 9100; a USB real device needs ``iproxy 9100 9100`` alongside the
    usual 8100 forward). ffmpeg transcodes that stream into the same
    ``recording.mp4`` the report embeds. Use :func:`check_mjpeg_stream` first
    when you want to fail fast instead of best-effort.
    """

    def __init__(self, output: str, url: str):
        super().__init__(output)
        self.url = url

    def _build_cmd(self, ffmpeg: str) -> tuple[list[str], str] | None:
        cmd = [
            ffmpeg, "-y",
            "-f", "mjpeg",
            # The stream carries no timestamps; wallclock keeps the video at
            # real-time speed regardless of the stream's (variable) frame rate.
            "-use_wallclock_as_timestamps", "1",
            "-i", self.url,
            # Device frames can have odd dimensions; yuv420p needs even ones.
            "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            "-vcodec", "libx264",
            "-preset", "ultrafast",
            "-pix_fmt", "yuv420p",
            self.output,
        ]
        return cmd, f"mjpeg stream {self.url}"


def check_mjpeg_stream(url: str, timeout: float = 5.0) -> str | None:
    """Probe ``url`` for a live MJPEG stream; return an error string, or ``None`` when OK.

    Reads the first body byte so both failure modes are caught up front —
    connection refused (port not forwarded / WDA down) and connects-but-silent
    (wrong service on the port) — instead of a long run quietly recording
    nothing.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if not resp.read(1):
                return f"nothing streaming at {url} (connected, but no data)"
    except Exception as e:
        return f"cannot read MJPEG stream at {url} ({e})"
    return None


class AppiumScreenRecorder:
    """Record a device's screen through Appium's session recording API.

    Works for both Android (UiAutomator2 → screenrecord under the hood) and
    iOS (XCUITest → simctl / stream transcode on the Appium server). The video
    lives inside the session: ``stop()`` fetches it base64-encoded and writes
    ``output``, so it MUST run before ``driver.quit()`` — afterwards the
    recording is gone. Same best-effort contract as the other recorders.
    """

    def __init__(self, output: str, driver: Any):
        self.output = output
        self._driver = driver
        self._active = False

    @property
    def active(self) -> bool:
        return self._active

    def start(self) -> bool:
        # The drivers' default timeLimit (180s) silently truncates longer runs;
        # 1800s is the documented maximum. XCUITest's default codec is mjpeg,
        # which HTML5 <video> can't play, so iOS is pinned to h264. Older
        # drivers that reject these options get a bare start (their defaults).
        opts: dict[str, Any] = {"timeLimit": 1800, "forceRestart": True}
        try:
            caps = getattr(self._driver, "capabilities", None) or {}
            if str(caps.get("platformName", "")).lower() == "ios":
                opts["videoType"] = "libx264"
        except Exception:
            pass
        try:
            self._driver.start_recording_screen(**opts)
        except Exception:
            try:
                self._driver.start_recording_screen()
            except Exception as e:
                logger.warning("appium screen recording failed to start: %s", e)
                return False
        self._active = True
        logger.info("recording started (appium screen recording) -> %s", self.output)
        return True

    def stop(self, timeout: float = 10.0) -> str | None:
        if not self._active:
            return None
        self._active = False
        try:
            payload = self._driver.stop_recording_screen()
        except Exception as e:
            logger.warning("appium screen recording failed to stop: %s", e)
            return None
        try:
            data = base64.b64decode(payload or "")
        except (binascii.Error, ValueError, TypeError) as e:
            logger.warning("appium screen recording returned undecodable data: %s", e)
            return None
        if not data:
            logger.warning("appium screen recording returned no data")
            return None
        os.makedirs(os.path.dirname(os.path.abspath(self.output)), exist_ok=True)
        with open(self.output, "wb") as f:
            f.write(data)
        logger.info("recording saved: %s", self.output)
        return self.output


class AdbScreenRecorder:
    """Record an Android device's screen via ``adb shell screenrecord``.

    screenrecord runs on the device (nothing to install, no ffmpeg needed to
    capture) but hard-caps each invocation at 3 minutes, so a background
    thread chains segments back to back. ``stop()`` signals screenrecord with
    SIGINT (so it finalizes the mp4's moov atom), pulls the segments, and
    merges them with ffmpeg when there is more than one; without ffmpeg only
    the first segment becomes ``recording.mp4`` (with a warning — runs under
    3 minutes never need ffmpeg at all). No audio: screenrecord doesn't
    capture it. Note stop() SIGINTs every screenrecord on the device.
    """

    _SEGMENT_SECONDS = 180  # screenrecord's per-invocation hard cap

    def __init__(self, output: str, adb: list[str]):
        # ``adb`` is the base command including device selection, e.g.
        # ["/path/to/adb", "-s", "emulator-5554"].
        self.output = output
        self._adb = list(adb)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None
        self._remote: list[str] = []
        self._started = False

    @property
    def active(self) -> bool:
        return self._started and not self._stop.is_set()

    def start(self) -> bool:
        if self._started:
            return self.active
        self._started = True
        if not _find_ffmpeg():
            logger.warning(
                "recording: ffmpeg not found — a run longer than 3 minutes will "
                "only embed its first screenrecord segment"
            )
        self._thread = threading.Thread(
            target=self._record_loop, name="qira-adb-record", daemon=True
        )
        self._thread.start()
        logger.info("recording started (adb screenrecord) -> %s", self.output)
        return True

    def _record_loop(self) -> None:
        i = 0
        while not self._stop.is_set():
            # Host pid in the name so two runs on one device don't clobber
            # each other's segments.
            remote = f"/sdcard/qira_rec_{os.getpid()}_{i:03d}.mp4"
            began = time.monotonic()
            try:
                self._proc = subprocess.Popen(
                    [
                        *self._adb, "shell", "screenrecord",
                        "--time-limit", str(self._SEGMENT_SECONDS), remote,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError as e:
                logger.warning("adb screenrecord failed to start: %s", e)
                return
            self._remote.append(remote)
            rc = self._proc.wait()
            if self._stop.is_set():
                return
            if rc != 0 or time.monotonic() - began < 2:
                # Died immediately (no screenrecord binary, permission, device
                # gone) — bail instead of respawning it in a tight loop.
                logger.warning("adb screenrecord exited early (rc=%s); recording stopped", rc)
                return
            i += 1

    def stop(self, timeout: float = 10.0) -> str | None:
        if not self._started or self._stop.is_set():
            return None
        self._stop.set()
        try:
            # SIGINT → screenrecord finalizes the moov atom and exits cleanly.
            subprocess.run(
                [*self._adb, "shell", "pkill", "-2", "screenrecord"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            pass
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.wait(timeout=max(timeout, 5.0))
            except subprocess.TimeoutExpired:
                proc.terminate()
        if self._thread is not None:
            self._thread.join(timeout=max(timeout, 5.0))
        return self._collect()

    def _collect(self) -> str | None:
        """Pull every device-side segment, then merge into ``output``."""
        os.makedirs(os.path.dirname(os.path.abspath(self.output)), exist_ok=True)
        parts: list[str] = []
        for i, remote in enumerate(self._remote):
            local = f"{self.output}.part{i:03d}.mp4"
            try:
                subprocess.run(
                    [*self._adb, "pull", remote, local],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120,
                )
                subprocess.run(
                    [*self._adb, "shell", "rm", "-f", remote],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15,
                )
            except (OSError, subprocess.SubprocessError):
                logger.debug("failed to pull %s", remote, exc_info=True)
            if os.path.exists(local) and os.path.getsize(local) > 0:
                parts.append(local)
            elif os.path.exists(local):
                os.remove(local)
        if not parts:
            logger.warning("recording produced no valid file (adb screenrecord)")
            return None
        return self._merge(parts)

    def _merge(self, parts: list[str]) -> str | None:
        if len(parts) == 1:
            os.replace(parts[0], self.output)
            logger.info("recording saved: %s", self.output)
            return self.output
        ffmpeg = _find_ffmpeg()
        if ffmpeg:
            concat_list = self.output + ".concat.txt"
            with open(concat_list, "w", encoding="utf-8") as f:
                for p in parts:
                    # concat-demuxer quoting: wrap in single quotes, escape any.
                    f.write("file '%s'\n" % os.path.abspath(p).replace("'", "'\\''"))
            try:
                subprocess.run(
                    [
                        ffmpeg, "-y", "-f", "concat", "-safe", "0",
                        "-i", concat_list, "-c", "copy", self.output,
                    ],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    timeout=300, check=True,
                )
            except (OSError, subprocess.SubprocessError):
                logger.warning("ffmpeg concat failed; embedding only the first segment")
            else:
                for p in parts:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                logger.info("recording saved: %s (%d segments)", self.output, len(parts))
                return self.output
            finally:
                try:
                    os.remove(concat_list)
                except OSError:
                    pass
        else:
            logger.warning(
                "ffmpeg not found; embedding only the first of %d screenrecord "
                "segments (the rest are kept next to it as .partNNN.mp4)", len(parts),
            )
        os.replace(parts[0], self.output)
        return self.output


def device_recorder(output: str, target: Any) -> Recorder | None:
    """Best recorder for ``target``'s own (device) screen, or ``None``.

    Appium drivers (android + ios) expose the session recording API; an
    :class:`~qirabot.adb.AdbDevice` carries the adb base command for
    screenrecord. Anything else — browsers, pyautogui, WDA iOS — has no device
    stream here (WDA iOS records via :class:`MjpegStreamRecorder` instead).
    """
    if hasattr(target, "start_recording_screen"):
        return AppiumScreenRecorder(output, target)
    # qirabot's own AdbDevice exposes the ready-made base command (duck-typed
    # so this stays import-light).
    try:
        adb_cmd = getattr(target, "adb_command", None)
    except Exception:
        adb_cmd = None  # serial resolution failed; recording is best-effort
    if adb_cmd:
        return AdbScreenRecorder(output, list(adb_cmd))
    return None


def record(
    report_dir: str,
    *,
    filename: str = "recording.mp4",
    fps: int = 12,
    window: str | None = None,
    region: tuple[int, int, int, int] | None = None,
    audio: bool | str = False,
    audio_offset: float | None = None,
) -> ScreenRecorder:
    """Convenience factory: a :class:`ScreenRecorder` writing ``report_dir/filename``.

    ``with record(bot.report_dir): ...`` records the full screen for the block.
    On Windows, ``region`` (physical-px ``(x, y, w, h)``, e.g. from
    :func:`window_region`) crops a desktop grab to one window — the
    GPU/game-safe path — while ``window`` (a title/handle) does per-window
    capture; ``audio`` (True to auto-detect system audio, or a dshow device
    name) adds sound. All degrade to full-screen / silent elsewhere.
    """
    return ScreenRecorder(
        os.path.join(report_dir, filename),
        fps=fps,
        window=window,
        region=region,
        audio=audio,
        audio_offset=audio_offset,
    )


def _env_truthy(value: str) -> bool:
    """Parse a boolean-ish env var value (``1``/``true``/``yes``/``on``)."""
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class RecordConfig:
    """Recording settings after merging constructor args with the QIRA_* env.

    Built once by :meth:`resolve`; the manager below reads it, the client
    never touches the individual flags again.
    """

    record: bool = False
    fps: int = 12
    # Windows-only extras: follow the window under test (resolved lazily from
    # the first action's target) and capture system audio. By default
    # record-window crops a desktop grab to the window's visible rect (works
    # for GPU/game windows the per-window path renders black);
    # ``window_native`` forces the legacy gdigrab per-window mode, which can
    # follow a background/occluded *non-GPU* window but goes black on games.
    window: bool = False
    window_native: bool = False
    audio: bool | str = False
    audio_offset: float | None = None
    # Record an MJPEG stream (WDA's device-screen stream, port 9100) instead
    # of the host screen — the only way recording can capture an iOS device's
    # screen rather than the desktop the SDK runs on.
    mjpeg_url: str | None = None
    # Record the automated device's own screen: the recorder is picked from
    # the first action's target (Appium driver → session recording API;
    # AdbDevice → adb screenrecord), so the start is deferred like window's.
    device: bool = False

    @classmethod
    def resolve(
        cls,
        *,
        record: bool = False,
        fps: int = 12,
        window: bool = False,
        audio: bool | str = False,
        audio_offset: float | None = None,
        mjpeg_url: str | None = None,
        device: bool = False,
    ) -> "RecordConfig":
        """Merge explicit arguments with their QIRA_* env fallbacks.

        Raises ``ValueError`` for an unparsable ``QIRA_AUDIO_OFFSET``.
        """
        if audio_offset is None:
            env_off = os.environ.get("QIRA_AUDIO_OFFSET", "")
            if env_off:
                try:
                    audio_offset = float(env_off)
                except ValueError:
                    raise ValueError(
                        f"QIRA_AUDIO_OFFSET must be a number, got {env_off!r}"
                    )
        return cls(
            record=record or _env_truthy(os.environ.get("QIRA_RECORD", "")),
            fps=fps,
            window=window or _env_truthy(os.environ.get("QIRA_RECORD_WINDOW", "")),
            window_native=_env_truthy(os.environ.get("QIRA_RECORD_WINDOW_NATIVE", "")),
            audio=audio or _env_truthy(os.environ.get("QIRA_RECORD_AUDIO", "")),
            audio_offset=audio_offset,
            mjpeg_url=mjpeg_url or os.environ.get("QIRA_RECORD_MJPEG_URL", "") or None,
            device=device or _env_truthy(os.environ.get("QIRA_RECORD_DEVICE", "")),
        )


class RecordingManager:
    """One Qirabot session's recorder slot and auto/deferred start flow.

    Owns everything ``Qirabot(record=...)`` implies: which :class:`Recorder`
    implementation fills the slot (host screen / MJPEG stream / device
    session), the deferred start for window/device modes (they need the first
    action's target), and the re-entrancy guard that keeps the auto path and
    manual ``start_recording()`` from ever spawning two ffmpegs. The client
    only forwards its public start/stop calls and the per-action
    :meth:`maybe_start` hook.
    """

    def __init__(
        self,
        cfg: RecordConfig,
        *,
        want_recording: bool = True,
        output_dir: "Callable[[], str]",
        window_info: "Callable[[Any], dict[str, Any] | None] | None" = None,
    ) -> None:
        """``output_dir`` is called lazily (the run directory is created on
        first use); ``window_info`` resolves an action target to the client's
        window metadata (title/hwnd) for window-following — any exception it
        raises degrades to full-screen."""
        self.cfg = cfg
        self.recorder: Recorder | None = None
        # Epoch time the current recording started; anchors the report's
        # per-step video-seek offsets. 0.0 = no recording started this run.
        self.started_ts = 0.0
        # True while a recording is still owed: claimed (set False) right
        # before a recorder starts, which also guards against re-entrancy when
        # window-following resolves the target through the client's adapter
        # lookup.
        self.pending = (cfg.record or cfg.device or bool(cfg.mjpeg_url)) and want_recording
        self._output_dir = output_dir
        self._window_info = window_info

    @property
    def active(self) -> bool:
        """True while a recorder is running."""
        return self.recorder is not None and self.recorder.active

    def maybe_start(self, target: Any = None) -> None:
        """Auto-start when recording was requested.

        Called once at client construction (no ``target``) and again on every
        action (with the action's ``target``). Skipped once a recorder exists
        or the slot has been claimed. In window/device mode the start is
        deferred until an action supplies a ``target`` to resolve the
        window/device from. Best-effort via :meth:`start` — a missing ffmpeg /
        unsupported platform warns.
        """
        if self.recorder is not None or not self.pending:
            return
        if (self.cfg.window or self.cfg.device) and target is None and not self.cfg.mjpeg_url:
            return  # defer: need a target to resolve the window/device from
        # Claim the slot BEFORE starting so the adapter lookup made while
        # resolving the window doesn't re-enter this and start a second ffmpeg.
        self.pending = False
        self.start(target=target)

    def start(
        self,
        *,
        fps: int | None = None,
        target: Any = None,
        window: str | None = None,
        audio: bool | str | None = None,
    ) -> bool:
        """Start a recorder into ``<output_dir>/recording.mp4``.

        Semantics documented on :meth:`qirabot.Qirabot.start_recording` (the
        public wrapper). Idempotent while a recording runs; best-effort —
        returns ``False`` and only warns when ffmpeg is missing or the
        platform is unsupported.
        """
        if self.recorder is not None and self.recorder.active:
            logger.info("recording already in progress; ignoring start_recording()")
            return True
        # Manual start also claims the slot so a later action's auto-start
        # hook doesn't spawn a second recorder.
        self.pending = False
        output = os.path.join(self._output_dir(), "recording.mp4")
        recorder: Recorder | None
        if self.cfg.mjpeg_url:
            # Device-screen stream (WDA MJPEG): window/region/audio are
            # host-screen concepts and don't apply.
            recorder = MjpegStreamRecorder(output, self.cfg.mjpeg_url)
        elif self.cfg.device:
            # Device-screen recording resolved from the action target (Appium
            # driver / AdbDevice). Falling back to the host screen would
            # record the wrong thing (the desktop the SDK runs on), so an
            # unsupported target skips recording — the report then carries the
            # requested-but-not-produced notice.
            recorder = device_recorder(output, target)
            if recorder is None:
                logger.warning(
                    "record: don't know how to record the device screen for %s "
                    "(need an Appium driver or an AdbDevice target); recording skipped",
                    type(target).__name__,
                )
                return False
        else:
            region: tuple[int, int, int, int] | None = None
            if window is None and target is not None and self.cfg.window:
                # Default: crop a desktop grab to the window's visible rect
                # (GPU/game safe). Fall back to legacy per-window capture when
                # forced via QIRA_RECORD_WINDOW_NATIVE or when the rect can't
                # be resolved (non-Windows, no hwnd, DWM off).
                if not self.cfg.window_native:
                    region = self._resolve_window_region(target)
                if region is None:
                    window = self._resolve_window_target(target)
            audio_spec = audio if audio is not None else self.cfg.audio
            recorder = ScreenRecorder(
                output,
                fps=fps if fps is not None else self.cfg.fps,
                window=window,
                region=region,
                audio=audio_spec,
                audio_offset=self.cfg.audio_offset,
            )
        started = recorder.start()
        self.recorder = recorder if started else None
        if started:
            # A restart overwrites recording.mp4 from scratch, so the anchor
            # moves with it.
            self.started_ts = time.time()
        return started

    def stop(self) -> str | None:
        """Stop the current recording and return the saved path (or ``None``).

        A no-op returning ``None`` when nothing is recording.
        """
        if self.recorder is None:
            return None
        recorder = self.recorder
        self.recorder = None
        try:
            return recorder.stop()
        except Exception:
            logger.debug("failed to stop recording", exc_info=True)
            return None

    def _resolve_window_target(self, target: Any) -> str | None:
        """Window title (or handle) to record for ``target``, or ``None``.

        Reads the client-supplied ``window_info`` callback (only the Windows
        window backend returns one); prefers the title, falls back to the
        numeric handle. Any failure degrades to ``None`` (full-screen).
        """
        try:
            info = self._window_info(target) if self._window_info else None
        except Exception:
            logger.debug("window_info() failed; recording full screen", exc_info=True)
            return None
        if not info:
            return None
        title = info.get("title")
        if title:
            return str(title)
        hwnd = info.get("hwnd")
        return str(hwnd) if hwnd is not None else None

    def _resolve_window_region(self, target: Any) -> tuple[int, int, int, int] | None:
        """Visible (x, y, w, h) of ``target``'s window for desktop-crop capture.

        Resolves the ``window_info`` hwnd to its physical-px rect via
        :func:`window_region`. Returns ``None`` (so the caller degrades to
        per-window or full-screen) when there's no hwnd or the rect can't be
        resolved.
        """
        try:
            info = self._window_info(target) if self._window_info else None
        except Exception:
            logger.debug("window_info() failed; not using region capture", exc_info=True)
            return None
        hwnd = info.get("hwnd") if info else None
        if hwnd is None:
            return None
        return window_region(int(hwnd))
