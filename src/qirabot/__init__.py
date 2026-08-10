"""Qirabot - AI automation bolt-on for any framework."""

from importlib.metadata import PackageNotFoundError, version

from qirabot._applaunch import launch_app
from qirabot._dotenv import load_dotenv
from qirabot.adapters.auto import register_adapter
from qirabot.adapters.base import DeviceAdapter, DeviceInfo, ScreenshotConfig
from qirabot.adb import AdbDevice
from qirabot.wda import WdaClient
from qirabot.windows import Window
from qirabot.client import (
    ExtractResult,
    LocateResult,
    Qirabot,
    RunResult,
    RunStatus,
    SessionUsage,
    StepResult,
    VerifyResult,
)
from qirabot.overlay import Overlay
from qirabot.exceptions import (
    ActionError,
    AuthenticationError,
    MissingDependencyError,
    QirabotError,
    QirabotTimeoutError,
)
from qirabot.recording import (
    AdbScreenRecorder,
    AppiumScreenRecorder,
    MjpegStreamRecorder,
    ScreenRecorder,
    record,
    window_region,
)

try:
    __version__ = version("qirabot")
except PackageNotFoundError:  # not installed (e.g. running from a source tree)
    __version__ = "0.0.0+unknown"

__all__ = [
    "__version__",
    "ActionError",
    "AdbDevice",
    "AdbScreenRecorder",
    "AppiumScreenRecorder",
    "AuthenticationError",
    "DeviceAdapter",
    "DeviceInfo",
    "ExtractResult",
    "ScreenshotConfig",
    "LocateResult",
    "MissingDependencyError",
    "MjpegStreamRecorder",
    "Overlay",
    "Qirabot",
    "QirabotError",
    "QirabotTimeoutError",
    "RunResult",
    "RunStatus",
    "ScreenRecorder",
    "SessionUsage",
    "StepResult",
    "VerifyResult",
    "WdaClient",
    "Window",
    "launch_app",
    "load_dotenv",
    "record",
    "register_adapter",
    "window_region",
]
