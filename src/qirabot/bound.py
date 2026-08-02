"""Target-bound proxy for :class:`Qirabot` (see ``Qirabot.bind``)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from qirabot.client import (
        ExtractResult,
        LocateResult,
        Qirabot,
        RunResult,
        StepResult,
        VerifyResult,
    )


class _BoundQirabot:
    """A :class:`Qirabot` proxy with a target pre-bound (see ``Qirabot.bind``).

    Action methods drop the leading ``target`` argument; everything else
    (lifecycle, context manager, unknown attributes) delegates to the wrapped
    ``Qirabot``. Methods that return the current target (tab-following) update
    the bound target in place, so the proxy keeps following the active page on
    Playwright while staying a no-op on single-target frameworks.
    """

    def __init__(self, bot: Qirabot, target: Any) -> None:
        self._bot = bot
        self._target = target

    @property
    def bot(self) -> Qirabot:
        """The underlying :class:`Qirabot` (rarely needed)."""
        return self._bot

    def _rebind(self, result: Any) -> Any:
        # Follow tab/window switches: the action's return value is the current
        # target, which may differ from the one we sent (Playwright new tab).
        self._target = result
        return result

    # -- action methods: inject the bound target ---------------------------

    def click(
        self,
        locate: str,
        *,
        modifier: str = "",
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        return self._rebind(
            self._bot.click(
                self._target,
                locate,
                modifier=modifier,
                timeout=timeout,
                interval=interval,
                wait=wait,
                retry=retry,
                thinking_level=thinking_level,
                language=language,
            )
        )

    def type_text(
        self,
        locate: str,
        text: str,
        *,
        press_enter: bool = False,
        clear_before_typing: bool = False,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        return self._rebind(
            self._bot.type_text(
                self._target,
                locate,
                text,
                press_enter=press_enter,
                clear_before_typing=clear_before_typing,
                timeout=timeout,
                interval=interval,
                wait=wait,
                retry=retry,
                thinking_level=thinking_level,
                language=language,
            )
        )

    def double_click(
        self,
        locate: str,
        *,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        return self._rebind(
            self._bot.double_click(
                self._target,
                locate,
                timeout=timeout,
                interval=interval,
                wait=wait,
                retry=retry,
                thinking_level=thinking_level,
                language=language,
            )
        )

    def long_press(
        self,
        locate: str,
        *,
        duration: float = 2.0,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        return self._rebind(
            self._bot.long_press(
                self._target,
                locate,
                duration=duration,
                timeout=timeout,
                interval=interval,
                wait=wait,
                retry=retry,
                thinking_level=thinking_level,
                language=language,
            )
        )

    def mouse_down(
        self,
        locate: str,
        *,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        return self._rebind(
            self._bot.mouse_down(
                self._target,
                locate,
                timeout=timeout,
                interval=interval,
                wait=wait,
                retry=retry,
                thinking_level=thinking_level,
                language=language,
            )
        )

    def mouse_up(
        self,
        locate: str = "",
        *,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> Any:
        return self._rebind(
            self._bot.mouse_up(
                self._target,
                locate,
                timeout=timeout,
                interval=interval,
                wait=wait,
                retry=retry,
                thinking_level=thinking_level,
                language=language,
            )
        )

    def extract(
        self,
        instruction: str,
        *,
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> ExtractResult:
        return self._bot.extract(
            self._target,
            instruction,
            retry=retry,
            thinking_level=thinking_level,
            language=language,
        )

    def verify(
        self,
        assertion: str,
        *,
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> VerifyResult:
        return self._bot.verify(
            self._target,
            assertion,
            retry=retry,
            thinking_level=thinking_level,
            language=language,
        )

    def locate(
        self,
        locate: str,
        *,
        timeout: float = 0.0,
        interval: float = 2.0,
        wait: str = "",
        retry: int | None = None,
        thinking_level: str = "",
        language: str = "",
    ) -> LocateResult:
        return self._bot.locate(
            self._target,
            locate,
            timeout=timeout,
            interval=interval,
            wait=wait,
            retry=retry,
            thinking_level=thinking_level,
            language=language,
        )

    def wait_for(
        self,
        assertion: str,
        timeout: float = 30.0,
        interval: float = 2.0,
        *,
        thinking_level: str = "",
        language: str = "",
    ) -> "_BoundQirabot":
        """Wait until ``assertion`` holds, else raise :class:`QirabotTimeoutError`.

        Returns the bound handle (unchanged target) so calls can be chained, e.g.
        ``bot.wait_for("公告出现").click("关闭公告")``.
        """
        self._bot.wait_for(
            self._target,
            assertion,
            timeout,
            interval,
            thinking_level=thinking_level,
            language=language,
        )
        return self

    def ai(
        self,
        instruction: str,
        max_steps: int = 20,
        *,
        on_step: Callable[["StepResult"], None] | None = None,
        thinking_level: str = "",
        language: str = "",
        custom_tools: list[Callable[..., Any] | dict[str, Any]] | None = None,
        exclude_tools: list[str] | None = None,
        knowledge: str | Path | list[str | Path] | None = None,
    ) -> "RunResult":
        return self._bot.ai(
            self._target,
            instruction,
            max_steps,
            on_step=on_step,
            thinking_level=thinking_level,
            language=language,
            custom_tools=custom_tools,
            exclude_tools=exclude_tools,
            knowledge=knowledge,
        )

    def screenshot(self) -> Path | None:
        return self._bot.screenshot(self._target)

    def go_back(self) -> Any:
        return self._rebind(self._bot.go_back(self._target))

    def press_key(self, key: str, duration_seconds: float = 0) -> Any:
        return self._rebind(
            self._bot.press_key(self._target, key, duration_seconds)
        )

    def key_down(self, key: str) -> Any:
        return self._rebind(self._bot.key_down(self._target, key))

    def key_up(self, key: str) -> Any:
        return self._rebind(self._bot.key_up(self._target, key))

    def close_tab(self) -> Any:
        return self._rebind(self._bot.close_tab(self._target))

    def navigate(self, url: str) -> Any:
        return self._rebind(self._bot.navigate(self._target, url))

    def scroll(
        self,
        direction: str = "down",
        distance: int = 3,
        *,
        x: float | None = None,
        y: float | None = None,
    ) -> None:
        self._bot.scroll(self._target, direction, distance, x=x, y=y)

    def current_page(self) -> Any:
        """The live current target (always fresh — use for native interop)."""
        return self._rebind(self._bot.current_page(self._target))

    # -- high-frequency lifecycle: explicit for typing/IDE -----------------

    def close(self) -> None:
        self._bot.close()

    def open(self, *args: Any, **kwargs: Any) -> Any:
        return self._bot.open(*args, **kwargs)

    @property
    def task_id(self) -> str | None:
        return self._bot.task_id

    # -- everything else (launch_app/fail/cancel/…) delegates --------------

    def __getattr__(self, name: str) -> Any:
        # __getattr__ runs only when normal lookup fails; guard _bot to avoid
        # recursion before __init__ has set it.
        if name == "_bot":
            raise AttributeError(name)
        return getattr(self._bot, name)

    def __enter__(self) -> _BoundQirabot:
        self._bot.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self._bot.__exit__(exc_type, exc_val, exc_tb)
