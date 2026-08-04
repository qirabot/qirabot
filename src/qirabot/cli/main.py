"""Qirabot CLI entry point."""

from __future__ import annotations

import dataclasses
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, TypeVar

import click
from click.core import ParameterSource

from qirabot._browser import launch_browser
from qirabot._dotenv import load_dotenv
from qirabot._optional import extra_install_hint, package_install_hint, require
from qirabot.cli.skill import skill
from qirabot.exceptions import QirabotError
from qirabot.report import format_action_details


def _make_bot(
    ctx: click.Context,
    model: str = "",
    thinking_level: str = "",
    media_resolution: str = "",
    language: str = "",
    report: bool = True,
    report_dir: str = "",
    annotate: bool = True,
    record: bool = False,
    record_mjpeg_url: str = "",
    record_device: bool = False,
    record_window: bool = False,
    task_name: str = "",
    overlay: bool = False,
    output_format: str = "text",
) -> Any:
    from qirabot import Qirabot

    try:
        return Qirabot(
            model=model,
            vertex_project=ctx.obj.get("vertex_project", ""),
            vertex_location=ctx.obj.get("vertex_location", ""),
            vertex_api_key=ctx.obj.get("vertex_api_key", ""),
            gemini_api_key=ctx.obj.get("gemini_api_key", ""),
            thinking_level=thinking_level,
            media_resolution=media_resolution,
            language=language,
            task_name=task_name,
            report=report,
            report_dir=report_dir,
            screenshot_annotate=annotate,
            record=record,
            record_mjpeg_url=record_mjpeg_url or None,
            record_device=record_device,
            record_window=record_window,
            overlay=overlay,
        )
    except Exception as e:
        # Engine construction already produces actionable messages (missing
        # ADC credentials, unknown provider, missing project), so str(e)
        # prints cleanly as-is. No bot exists yet, so the machine-format
        # result carries null task_id/usage/report.
        if output_format in _MACHINE_FORMATS:
            _emit_json(_json_result(None, success=False, status="error", output=str(e)))
        else:
            click.echo(f"Error: {e}", err=True)
        sys.exit(1)


def _print_usage(console: Any, bot: Any) -> None:
    """One dim summary line after the run outcome: steps · tokens · duration.

    Printed on every terminal path — success, failure and cancel alike (an
    aborted run's tokens are still spent). Mirrors the report header's
    format and token semantics (total = input + cache read/write + output;
    thinking is already inside output). Silent when nothing ran, and never
    lets a formatting problem mask the run outcome that was already printed.
    """
    from qirabot.report import fmt_ms, fmt_tokens

    try:
        u = bot.usage
        if not u.ai_steps and not u.total_tokens:
            return
        bits = [f"{u.ai_steps} AI step{'s' if u.ai_steps != 1 else ''}"]
        if u.total_tokens:
            # Mirrors the report header: cache read/write shown as one
            # number when present, think only when a provider reports it
            # separately (Anthropic-style providers fold it into output,
            # leaving 0).
            cache = u.cache_read_tokens + u.cache_write_tokens
            detail = [f"in {fmt_tokens(u.input_tokens)}"]
            if cache:
                detail.append(f"cache {fmt_tokens(cache)}")
            detail.append(f"out {fmt_tokens(u.output_tokens)}")
            if u.thinking_tokens:
                detail.append(f"think {fmt_tokens(u.thinking_tokens)}")
            bits.append(f"{fmt_tokens(u.total_tokens)} tokens ({' / '.join(detail)})")
        if u.step_duration_ms:
            bits.append(fmt_ms(u.step_duration_ms))
        console.print(f"[dim]{' · '.join(bits)}[/dim]")
    except Exception:
        pass


# Formats whose stdout carries machine-readable JSON only: "json" prints one
# result object at the end of the run; "stream-json" additionally prints a
# start line and one NDJSON line per step. All rich/human output is suppressed
# in these formats so stdout stays parseable by scripts and CI.
_MACHINE_FORMATS = ("json", "stream-json")


def _emit_json(obj: dict[str, Any]) -> None:
    # Flushed per line so stream-json consumers see steps as they happen,
    # not when the pipe buffer fills.
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def _json_result(bot: Any, *, success: bool, status: str, output: str) -> dict[str, Any]:
    """The terminal JSON object of a run — one schema on every exit path
    (done / failed / error / cancelled), so scripts parse a single shape.

    ``status`` extends the SDK's RunStatus with ``"cancelled"`` (Ctrl+C or the
    ESC kill switch). Field names mirror the SDK's public attributes verbatim
    (``task_id``, SessionUsage's fields) so the two surfaces never drift.
    ``report`` is the path close() writes on exit — the emit happens *before*
    that write, so consumers should read the file after the process exits.
    ``bot`` is None only when engine construction itself failed.
    """
    usage = None
    report = None
    task_id = None
    if bot is not None:
        task_id = bot.task_id
        u = bot.usage
        usage = {**dataclasses.asdict(u), "total_tokens": u.total_tokens}
        try:
            # will_write_report mirrors _write_report's own gate — otherwise
            # close() writes nothing and the path would dangle. Never let
            # this probe mask the run outcome.
            if bot.will_write_report:
                report = str(Path(bot.report_dir) / "report.html")
        except Exception:
            report = None
    return {
        "type": "result",
        "success": success,
        "status": status,
        "output": output,
        "task_id": task_id,
        "usage": usage,
        "report": report,
    }


def _run_local(
    bot: Any,
    target: Any,
    instruction: str,
    max_steps: int,
    knowledge: str = "",
    output_format: str = "text",
) -> None:
    from rich.console import Console
    from rich.markup import escape

    machine = output_format in _MACHINE_FORMATS
    stream = output_format == "stream-json"
    console = Console()
    indent = " " * (len(f"[{max_steps}/{max_steps}]") + 1)

    if bot.task_id and not machine:
        console.print(f"[dim]Run:[/dim] {bot.task_id}")
    if stream:
        _emit_json({"type": "start", "task_id": bot.task_id, "max_steps": max_steps})

    def on_step_text(step: Any) -> None:
        if step.action_type == "done":
            return
        detail_parts = format_action_details(step.params or {})
        label = escape(f"[{step.step}/{max_steps}]")
        head = f"[bold cyan]{label}[/bold cyan] [yellow]{step.action_type}[/yellow]"
        if detail_parts:
            head += "  " + escape("  ".join(detail_parts))
        console.print(head)
        if step.decision:
            console.print(f"{indent}[dim]└ {escape(step.decision)}[/dim]")

    def on_step_stream(step: Any) -> None:
        # asdict keeps the NDJSON field names identical to StepResult's — and
        # unlike the text callback the "done" step is included: its decision
        # and output are data a consumer may want, not screen noise.
        _emit_json({"type": "step", **dataclasses.asdict(step)})

    on_step = on_step_stream if stream else None if machine else on_step_text

    def finish(*, success: bool, status: str, output: str, code: int, text: str) -> NoReturn | None:
        """One terminal line per run: the JSON result object in the machine
        formats, the colored outcome line otherwise. code=0 returns so the
        caller's finally (close → report write) still runs before exit 0."""
        if machine:
            _emit_json(_json_result(bot, success=success, status=status, output=output))
        else:
            console.print(text)
        if code:
            sys.exit(code)
        return None

    # The finally prints the usage summary after the outcome line on every
    # exit path — Done, Failed, Error and both Cancelled forms (sys.exit's
    # SystemExit passes through it). A cancelled run's tokens are still spent.
    # (Machine formats skip it: usage is inside the JSON result object.)
    try:
        try:
            result = bot.ai(
                target, instruction, max_steps=max_steps, on_step=on_step,
                knowledge=knowledge or None,
            )
        except KeyboardInterrupt:
            # Ctrl+C raises KeyboardInterrupt, which is a BaseException — NOT caught
            # by `except Exception` below. Without this branch the interrupt would
            # skip reporting and the caller's finally:bot.close() would complete the
            # still-running task as succeeded. Report it as a deliberate cancel so
            # the run lands in the 'cancelled' bucket, not 'failed' or 'succeeded'.
            # (130 = 128 + SIGINT, the conventional Ctrl+C exit code.)
            bot.cancel("aborted by user")
            finish(
                success=False, status="cancelled", output="aborted by user",
                code=130, text="\n[bold yellow]Cancelled[/bold yellow]",
            )
        except QirabotError as e:
            if getattr(e, "code", "") == "user_abort":
                # ESC-hold kill switch: the same deliberate cancel as Ctrl+C
                # above, so it gets the same face — yellow, exit 130, never a
                # red Error. ai() already routed it through cancel(), so the
                # terminal state is recorded; no fail() here.
                finish(
                    success=False, status="cancelled", output="aborted by user (ESC held)",
                    code=130, text="\n[bold yellow]Cancelled[/bold yellow] (ESC held)",
                )
            bot.fail(str(e))
            finish(
                success=False, status="error", output=str(e),
                code=1, text=f"[bold red]Error:[/bold red] {escape(str(e))}",
            )
        except Exception as e:
            # Report the client-side abort so the task is recorded as failed; without
            # this the bot.close() in the caller's finally would complete the
            # still-running task as succeeded. (Transport already collapses HTML
            # error bodies to one-line summaries, so str(e) prints cleanly.)
            bot.fail(str(e))
            finish(
                success=False, status="error", output=str(e),
                code=1, text=f"[bold red]Error:[/bold red] {escape(str(e))}",
            )
        if result.success:
            finish(
                success=True, status=result.status, output=result.output,
                code=0, text=f"[bold green]Done:[/bold green] {result.output}",
            )
        else:
            # The client already set a terminal status for known failures (e.g. max
            # steps); fail() is idempotent there and ensures other failure paths are
            # not left to close()'s success default.
            bot.fail(result.output)
            # Non-zero exit so scripts/CI can detect an unfinished task; SystemExit
            # bypasses the caller's `except Exception` and still runs its finally.
            finish(
                success=False, status=result.status, output=result.output,
                code=1, text=f"[bold red]Failed:[/bold red] {result.output}",
            )
    finally:
        if not machine:
            _print_usage(console, bot)


def _fail_setup(bot: Any, e: Exception, output_format: str = "text") -> NoReturn:
    """Report a setup-phase failure (before _run_local takes over) and exit.

    Setup — bot.open() for browser, Appium Remote() / device resolution for
    android/ios/desktop — runs after the bot is constructed but before
    _run_local starts reporting outcomes. An
    error there leaves the task un-terminalized, so the command's
    finally:bot.close() would otherwise complete it as *succeeded*. Record it as
    failed instead, print the error, and exit 1. Machine formats get the same
    JSON result object as a run failure, so scripts parse one schema.
    """
    bot.fail(str(e))
    if output_format in _MACHINE_FORMATS:
        _emit_json(_json_result(bot, success=False, status="error", output=str(e)))
    else:
        click.echo(f"Error: {e}", err=True)
    sys.exit(1)


def _default_task_name(instruction: str) -> str:
    """Derive a task name from the instruction when --name is not given, so
    runs are distinguishable in the report header instead of sharing one name."""
    first_line = next((ln.strip() for ln in instruction.splitlines() if ln.strip()), "")
    return first_line[:60] or "cli"


# Accept -h alongside --help, and print each option's default in --help. The
# show_default context setting is inherited by every subcommand (click >= 8.1).
_CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "show_default": True,
}

_FC = TypeVar("_FC", bound=Callable[..., Any])

# --help group headings for the task commands. The two shared groups say
# "(all platforms)" so users can see at a glance which flags carry over
# between browser/android/ios/desktop.
_TASK_GROUP = "Task options (all platforms)"
_DEBUG_GROUP = "Report & debug options (all platforms)"


def _option(*decls: str, group: str, **attrs: Any) -> Callable[[_FC], _FC]:
    """click.option that tags the option with a --help group heading, rendered
    by _GroupedCommand. Groups appear in declaration (reading) order."""

    def deco(f: _FC) -> _FC:
        f = click.option(*decls, **attrs)(f)
        f.__click_params__[-1].help_group = group  # type: ignore[attr-defined]
        return f

    return deco


class _GroupedCommand(click.Command):
    """Command whose --help lists options under group headings instead of one
    flat list. Untagged params (just the trailing --help in practice) land
    under "Other options"."""

    def format_options(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        groups: dict[str, list[tuple[str, str]]] = {}
        for param in self.get_params(ctx):
            record = param.get_help_record(ctx)
            if record is None:
                continue
            title = getattr(param, "help_group", "") or "Other options"
            groups.setdefault(title, []).append(record)
        for title, records in groups.items():
            with formatter.section(title):
                formatter.write_dl(records)


def _resolve_knowledge_cb(
    ctx: click.Context, param: click.Parameter, value: tuple[Path, ...]
) -> str:
    """Resolve --knowledge files into the final text at parse time, so UTF-8 and
    size errors surface before any task is created or browser/device opened.
    click.Path already guarantees each file exists."""
    if not value:
        return ""
    from qirabot._knowledge import resolve_knowledge

    try:
        return resolve_knowledge(list(value))
    except ValueError as e:
        raise click.BadParameter(str(e)) from None


def _task_options(f: _FC) -> _FC:
    """Task options shared by browser/android/ios/desktop. Applied in reverse so
    --help lists them in reading order (name, model, thinking-level,
    media-resolution, language, max-steps, knowledge, output-format)."""
    f = _option(
        "--output-format", group=_TASK_GROUP, default="text",
        type=click.Choice(["text", "json", "stream-json"]),
        help="Output format: text (human-readable), json (stdout carries one final "
        "JSON result object), stream-json (NDJSON: a start line, one line per step, "
        "then the result object)",
    )(f)
    # File paths only, never inline text: argv has no str/Path type split to
    # declare intent with, and sniffing is off the table (see _knowledge.py).
    # Inline snippets work through the shell: -k <(printf '...').
    f = _option(
        "--knowledge", "-k", group=_TASK_GROUP, multiple=True,
        type=click.Path(exists=True, dir_okay=False, path_type=Path),
        callback=_resolve_knowledge_cb,
        help="Knowledge file the AI consults during the task (UTF-8 text; repeatable, 32KB total)",
    )(f)
    f = _option("--max-steps", group=_TASK_GROUP, default=20, help="Max steps for AI")(f)
    f = _option("--language", "-l", group=_TASK_GROUP, default="", help="Language (e.g. zh, en)")(f)
    f = _option("--media-resolution", group=_TASK_GROUP, default="", help="Screenshot resolution sent to the model: low, medium, high or ultra_high (env QIRA_MEDIA_RESOLUTION; gemini-vertex only)")(f)
    f = _option("--thinking-level", group=_TASK_GROUP, default="", help="Thinking level override: minimal, low, medium or high")(f)
    f = _option(
        "--model", "-m", group=_TASK_GROUP, default="",
        help='Model as "{provider}/{model}" with provider one of gemini-vertex / '
        "gemini (default: QIRA_MODEL or the built-in default)",
    )(f)
    f = _option("--name", "-n", group=_TASK_GROUP, default="", help="Run name shown in the report (default: derived from the instruction)")(f)
    return f


def _debug_options(record: bool = True) -> Callable[[_FC], _FC]:
    """Debug options shared by browser/android/ios/desktop. This --record is
    the host-screen (ffmpeg) recorder, so it's opted out for android/ios —
    they define their own device-screen --record instead (grouped under the
    platform heading, since its semantics are platform-specific)."""

    def wrap(f: _FC) -> _FC:
        if record:
            f = _option("--record", group=_DEBUG_GROUP, is_flag=True, help="Record the screen to report-dir/recording.mp4 (requires ffmpeg)")(f)
        f = _option("--overlay/--no-overlay", group=_DEBUG_GROUP, default=True, help="Show task progress in a small bottom-right on-screen window — excluded from screenshots, click-through (macOS/Windows; no-op elsewhere)")(f)
        f = _option("--annotate/--no-annotate", group=_DEBUG_GROUP, default=True, help="Annotate saved screenshots with click coordinates")(f)
        f = _option("--report-dir", group=_DEBUG_GROUP, default="", help="Report output root (env QIRA_REPORT_DIR; default ./qira_runs/<date>/<run>/)")(f)
        f = _option("--report/--no-report", group=_DEBUG_GROUP, default=True, help="Write an HTML run report to --report-dir")(f)
        return f

    return wrap


@click.group(context_settings=_CONTEXT_SETTINGS)
@click.version_option(package_name="qirabot", prog_name="qirabot")
@click.option(
    "--vertex-project",
    envvar="QIRA_VERTEX_PROJECT",
    default="",
    help="Google Cloud project for the Vertex providers "
    "(default: GOOGLE_CLOUD_PROJECT or the ADC credentials' own project)",
)
@click.option(
    "--vertex-location",
    envvar="QIRA_VERTEX_LOCATION",
    default="",
    help="Vertex location (default: GOOGLE_CLOUD_LOCATION or global)",
)
@click.option(
    "--vertex-api-key",
    envvar="QIRA_VERTEX_API_KEY",
    default="",
    help="Vertex AI API key — auth without ADC/gcloud (gemini-vertex models "
    "only, global endpoint; overrides --vertex-project/--vertex-location)",
)
@click.option(
    "--gemini-api-key",
    envvar="QIRA_GEMINI_API_KEY",
    default="",
    help="Gemini Developer API (AI Studio) key for the gemini provider "
    "(also read from GEMINI_API_KEY)",
)
@click.pass_context
def cli(
    ctx: click.Context,
    vertex_project: str,
    vertex_location: str,
    vertex_api_key: str,
    gemini_api_key: str,
) -> None:
    """Qirabot CLI — AI automation tool.

    The decision engine runs locally against Vertex AI with your Google Cloud
    credentials (ADC): set GOOGLE_APPLICATION_CREDENTIALS to a service-account
    JSON or run `gcloud auth application-default login` once. For gemini-vertex
    models a Vertex AI API key works instead (--vertex-api-key or
    QIRA_VERTEX_API_KEY) — no gcloud setup needed. The gemini provider calls
    the Gemini Developer API with an AI Studio key (--gemini-api-key,
    QIRA_GEMINI_API_KEY or GEMINI_API_KEY).

    Global options (--vertex-project/--vertex-location/--vertex-api-key/
    --gemini-api-key) go before the subcommand, e.g.
    `qirabot --vertex-project my-proj browser "..."`.
    """
    ctx.ensure_object(dict)
    ctx.obj["vertex_project"] = vertex_project
    ctx.obj["vertex_location"] = vertex_location
    ctx.obj["vertex_api_key"] = vertex_api_key
    ctx.obj["gemini_api_key"] = gemini_api_key


@cli.command()
@click.pass_context
def models(ctx: click.Context) -> None:
    """List the built-in providers, their default models, and whether the
    configured auth (API keys and/or Google Cloud ADC) resolves on this
    machine."""
    from rich.console import Console
    from rich.table import Table

    from qirabot.engine.providers.registry import (
        DEFAULT_MODELS,
        SUPPORTED_PROVIDERS,
        resolve_default_model,
    )

    console = Console()
    table = Table(title="Providers")
    table.add_column("Provider", style="cyan")
    table.add_column("Default model")
    table.add_column("Example")
    for provider in SUPPORTED_PROVIDERS:
        default = DEFAULT_MODELS[provider]
        table.add_row(provider, default, f"{provider}/{default}")
    console.print(table)
    console.print(f"Session default: [cyan]{resolve_default_model()}[/cyan]  (override with --model or QIRA_MODEL)")

    if _vertex_api_key(ctx):
        console.print(
            "[green]✓[/green] Vertex AI API key configured — gemini-vertex via "
            "the global endpoint, no ADC needed"
        )
    if _gemini_api_key(ctx):
        console.print(
            "[green]✓[/green] Gemini API key configured — the gemini provider "
            "(Gemini Developer API / AI Studio), no ADC needed"
        )
    project, cred_err = _resolve_adc(ctx)
    if not cred_err:
        console.print(f"[green]✓[/green] Google Cloud credentials OK (project: {project})")
    elif _vertex_api_key(ctx) or _gemini_api_key(ctx):
        console.print(
            f"[yellow]![/yellow] Google Cloud credentials (ADC) unavailable — "
            f"{_adc_caveat(ctx, cred_err)}"
        )
    else:
        console.print(f"[red]✗[/red] Google Cloud credentials: {cred_err}")


def _vertex_api_key(ctx: click.Context) -> str:
    """The effective Vertex AI API key (--vertex-api-key > QIRA_VERTEX_API_KEY),
    empty when API-key auth is not configured."""
    from qirabot.engine.providers.registry import resolve_vertex_api_key

    return resolve_vertex_api_key(ctx.obj.get("vertex_api_key", ""))


def _gemini_api_key(ctx: click.Context) -> str:
    """The effective Gemini Developer API key (--gemini-api-key >
    QIRA_GEMINI_API_KEY > GEMINI_API_KEY), empty when not configured."""
    from qirabot.engine.providers.registry import resolve_gemini_api_key

    return resolve_gemini_api_key(ctx.obj.get("gemini_api_key", ""))


def _adc_caveat(ctx: click.Context, err: str) -> str:
    """The doctor/models caveat when ADC is missing but an API key is
    present: with a Vertex key nothing needs ADC anymore; with only a
    Gemini (AI Studio) key, gemini-vertex still does."""
    if _vertex_api_key(ctx):
        return "not needed with the configured API key"
    return f"only needed for gemini-vertex: {err}"


def _resolve_adc(ctx: click.Context) -> tuple[str, str]:
    """(project, error) — probe ADC + project resolution without any LLM call.

    Fetching an access token exercises the real credential path (service
    account, gcloud, or metadata server) so `doctor`/`models` report the same
    failure a task run would hit, at zero model cost.
    """
    from qirabot.engine.providers.registry import resolve_vertex_project
    from qirabot.engine.providers.vertex_auth import VertexTokenSource

    tokens = VertexTokenSource()
    try:
        tokens.token()
        project = resolve_vertex_project(ctx.obj.get("vertex_project", ""), tokens)
        return project, ""
    except Exception as e:
        return "", str(e)


@cli.command("install-browser")
def install_browser() -> None:
    """Download the Chromium that browser automation drives (one-time).

    Exists because isolated installs (`uv tool install`, the install script)
    don't put Playwright's own `playwright` command on PATH — this wraps the
    same Chromium download so the second setup step is identical everywhere.
    """
    require("playwright", "browser")
    import subprocess

    rc = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"]
    ).returncode
    if rc != 0:
        raise click.ClickException(f"Chromium download failed (exit {rc})")
    click.echo('Chromium installed — you\'re ready: qirabot browser "..."')


# `qirabot skill install/uninstall/list` — lives in cli/skill.py.
cli.add_command(skill)


def _parse_viewport(viewport: str) -> tuple[int, int]:
    try:
        w_str, h_str = viewport.lower().split("x")
        return (int(w_str), int(h_str))
    except ValueError:
        raise click.BadParameter(f"viewport must be WIDTHxHEIGHT, got '{viewport}'")


@cli.command("open-browser", cls=_GroupedCommand)
@_option("--url", "-u", group="Browser options", default="", help="URL to open, e.g. the site's login page")
@_option("--user-data-dir", group="Browser options", required=True, help="Profile directory to save the session into — pass the same directory to `qirabot browser` or bot.open() later")
@_option("--viewport", group="Browser options", default="1280x800", help="Viewport size as WIDTHxHEIGHT")
@_option("--channel", group="Browser options", default="", help="Browser channel: chrome, msedge, etc. (uses installed browser instead of bundled Chromium)")
@_option("--browser-arg", group="Browser options", multiple=True, help="Extra Chromium launch arg, repeatable")
def open_browser(
    url: str,
    user_data_dir: str,
    viewport: str,
    channel: str,
    browser_arg: tuple[str, ...],
) -> None:
    """Open a browser to log in to websites by hand — no AI task, no API key.

    Cookies and local storage persist in --user-data-dir, so AI runs that
    reuse the same directory (`qirabot browser ... --user-data-dir <dir>` or
    `bot.open(url, user_data_dir=<dir>)`) start already signed in. Close the
    browser window (or press Ctrl-C) when you're done; a profile directory
    can't be shared by two browsers at once, so close it before running tasks.
    """
    # Qirabot.open() would fall back to headless here, which is right for AI
    # tasks but useless for a browser that exists only to be clicked in.
    if not _display_available():
        raise click.ClickException(
            "no display detected (DISPLAY/WAYLAND_DISPLAY unset) — this command "
            "opens a visible browser for you to log in with, which cannot work "
            "here. Run it on a machine with a display, then copy the profile "
            "directory over."
        )
    vp = _parse_viewport(viewport)
    launched = launch_browser(
        url=url,
        headless=False,
        viewport=vp,
        user_data_dir=user_data_dir,
        channel=channel,
        args=list(browser_arg) if browser_arg else None,
    )
    click.echo("Browser is open — log in to the sites your automation needs.")
    click.echo("When you're done, close the browser window (or press Ctrl-C here).")
    try:
        launched.context.wait_for_event("close", timeout=0)
    except KeyboardInterrupt:
        pass
    finally:
        for close in (launched.context.close, launched.playwright.stop):
            try:
                close()
            except Exception:
                pass
    click.echo(f"Session saved to {user_data_dir}")
    click.echo(f'Next: qirabot browser "<your task>" --user-data-dir {user_data_dir}')


def _has_module(module: str) -> bool:
    """Probe an optional dependency without require()'s raise (doctor only).

    Catches every exception, not just ImportError: pyautogui raises KeyError
    at import time on a display-less Linux box (no $DISPLAY), and a probe
    must report "not usable here", never crash doctor.
    """
    import importlib

    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def _adb_binary_found() -> bool:
    """Probe the adb binary (doctor only) — the android backend is pure stdlib,
    so the binary, not a Python module, is the thing that can be missing."""
    from qirabot.adb import _which_adb

    return _which_adb() is not None


def _display_available() -> bool:
    """False only on Linux with no display server — headed launches would fail
    there, and Qirabot.open() falls back to headless with a warning."""
    if not sys.platform.startswith("linux"):
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def _chromium_status() -> str | None:
    """None = playwright not installed; else "ready", "no-browser", or "no-libs".

    Asking playwright for ``chromium.executable_path`` needs the driver process
    (~1s startup) — acceptable for a diagnostic command, and the only reliable
    answer: the download location moves with PLAYWRIGHT_BROWSERS_PATH and the
    bundled browser revision.

    "no-libs" (Linux only): the download exists but ``ldd`` reports unresolved
    shared libraries (e.g. libnspr4.so on a bare server), so launch would fail —
    the fix is ``playwright install-deps``, not a re-download.
    """
    if not _has_module("playwright.sync_api"):
        return None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            exe = p.chromium.executable_path
        if not os.path.exists(exe):
            return "no-browser"
    except Exception:
        return "no-browser"
    if sys.platform.startswith("linux"):
        import subprocess

        try:
            ldd = subprocess.run(
                ["ldd", exe], capture_output=True, text=True, timeout=10
            )
            if "not found" in ldd.stdout:
                return "no-libs"
        except Exception:
            pass  # no ldd / probe failure: don't fail a browser that may work
    return "ready"


@cli.command()
@click.pass_context
def doctor(ctx: click.Context) -> None:
    """Check the environment: Python, Google Cloud credentials, and each backend's deps.

    Exits 0 when at least one backend can run end-to-end (key accepted, backend
    installed), 1 otherwise — so setup scripts and CI can gate on it.
    """
    import shutil

    from rich.console import Console
    from rich.markup import escape

    console = Console()
    ok, bad, warn = "[green]✓[/green]", "[red]✗[/red]", "[yellow]![/yellow]"
    problems = 0

    py = ".".join(str(v) for v in sys.version_info[:3])
    if sys.version_info >= (3, 10):
        console.print(f"{ok} Python {py}")
    else:
        console.print(f"{bad} Python {py} — qirabot requires 3.10+")
        problems += 1

    # Google Cloud credentials: the engine runs locally, so auth + a project
    # are the whole "server" story now. Probing a token exercises the same
    # credential path a task run would use, at zero model cost. A configured
    # API key covers its provider on its own, so a missing ADC is then a
    # caveat, not a failure.
    from qirabot.engine.providers.registry import resolve_default_model

    api_key_mode = bool(_vertex_api_key(ctx)) or bool(_gemini_api_key(ctx))
    if _vertex_api_key(ctx):
        console.print(
            f"{ok} Vertex AI API key configured (model: "
            f"{escape(resolve_default_model())}) — gemini-vertex via the "
            "global endpoint, no ADC needed"
        )
    if _gemini_api_key(ctx):
        console.print(
            f"{ok} Gemini API key configured (model: "
            f"{escape(resolve_default_model())}) — the gemini provider "
            "(Gemini Developer API / AI Studio), no ADC needed"
        )
    with console.status("checking Google Cloud credentials (ADC)..."):
        project, cred_err = _resolve_adc(ctx)
    if not cred_err:
        console.print(
            f"{ok} Google Cloud credentials OK (project: {escape(project)}, "
            f"model: {escape(resolve_default_model())})"
        )
    elif api_key_mode:
        console.print(
            f"{warn} Google Cloud credentials (ADC) unavailable — "
            f"{escape(_adc_caveat(ctx, cred_err))}"
        )
    else:
        console.print(f"{bad} Google Cloud credentials: {escape(cred_err)}")
        problems += 1

    # A leftover v2 cloud key is the one setup state where doctor could say
    # "Ready" while every default run refuses to start: the SDK's migration
    # guard trips on QIRA_API_KEY unless a model is chosen explicitly.
    # Surface it here, where the user is already looking for setup problems.
    if os.environ.get("QIRA_API_KEY") and not os.environ.get("QIRA_MODEL", "").strip():
        from qirabot._dotenv import injected_from

        src = injected_from("QIRA_API_KEY")
        where = f"loaded from {escape(src)}" if src else "exported in the environment"
        console.print(
            f"{warn} leftover v2 QIRA_API_KEY ({where}) — the cloud backend is "
            "gone, and runs will refuse to start until you remove it (and "
            "QIRA_BASE_URL) or opt into v3 with --model / QIRA_MODEL"
        )
    from qirabot._userconfig import config_path, load_api_key

    if load_api_key():
        console.print(
            f"{warn} v2 credentials in {escape(str(config_path()))} — unused "
            "in v3; delete the file to clean up"
        )

    # (label, ready, fix-hint). A missing Chromium download or missing system
    # libraries both count as not-ready: bot.open() would fail at launch even
    # though the import succeeds.
    chromium = _chromium_status()
    chromium_hints = {
        # Not `playwright install chromium`: isolated installs (uv tool) don't
        # put Playwright's own command on PATH, and the wrapper works everywhere.
        "no-browser": "qirabot install-browser",
        "no-libs": "sudo playwright install-deps chromium  "
        "(Chromium is downloaded but system libraries are missing)",
    }
    browser_hint = extra_install_hint("browser") + " && qirabot install-browser"
    backends = [
        (
            "browser (Playwright — default path, powers bot.open())",
            chromium == "ready",
            chromium_hints.get(chromium or "", browser_hint),
        ),
        (
            "desktop (pyautogui)",
            _has_module("pyautogui"),
            extra_install_hint("desktop"),
        ),
        (
            "android direct (adb — built in, needs the adb binary)",
            _adb_binary_found(),
            "install Android platform-tools and put adb on PATH "
            "(https://developer.android.com/tools/releases/platform-tools)",
        ),
        (
            "android/ios via server (Appium)",
            _has_module("appium"),
            extra_install_hint("appium"),
        ),
        (
            "selenium (bring-your-own driver)",
            _has_module("selenium"),
            package_install_hint("selenium"),
        ),
    ]

    console.print("\n[bold]Backends[/bold] — you only need the one you plan to drive:")
    # Informational (not a gate): the direct iOS backend is built into the core
    # package; its only requirement — WebDriverAgent running on the device —
    # can't be probed from here.
    console.print(
        f"  {ok} ios direct (WDA — built in; needs WebDriverAgent running on the device)"
    )
    for label, ready, hint in backends:
        if ready:
            console.print(f"  {ok} {escape(label)}")
        else:
            console.print(f"  {bad} {escape(label)} — {escape(hint)}")
        if label.startswith("browser") and ready and not _display_available():
            console.print(
                f"    {warn} no display (DISPLAY unset) — headed windows can't open "
                "here; bot.open() and the CLI fall back to headless automatically"
            )

    if not any(ready for _, ready, _ in backends):
        console.print(
            f"\n{bad} No backend installed. Quickest start: " + escape(browser_hint)
        )
        problems += 1

    console.print("\n[bold]Optional[/bold]:")
    if shutil.which("ffmpeg"):
        console.print(f"  {ok} ffmpeg — screen recording (record=True) available")
    else:
        console.print(
            f"  {warn} ffmpeg not on PATH — record=True will warn and skip recording"
        )

    console.print()
    if problems:
        console.print("[bold red]Not ready[/bold red] — fix the ✗ items above.")
        sys.exit(1)
    ready_labels = ", ".join(label.split(" (")[0] for label, ready, _ in backends if ready)
    console.print(f"[bold green]Ready[/bold green] — usable backends: {escape(ready_labels)}.")


@cli.command(cls=_GroupedCommand)
@click.argument("instruction")
@_task_options
# Browser — basic
@_option("--url", "-u", group="Browser options", default="", help="URL to open (optional, AI navigates if omitted)")
@_option("--headless", group="Browser options", is_flag=True, help="Run browser in headless mode")
@_option("--viewport", group="Browser options", default="1280x800", help="Viewport size as WIDTHxHEIGHT")
# Browser — advanced
@_option("--channel", group="Browser options", default="", help="Browser channel: chrome, msedge, etc. (uses installed browser instead of bundled Chromium)")
@_option("--user-data-dir", group="Browser options", default="", help="Persistent browser profile directory (keeps cookies/history across runs)")
@_option("--browser-arg", group="Browser options", multiple=True, help="Extra Chromium launch arg, repeatable (e.g. --browser-arg=--disable-blink-features=AutomationControlled)")
@_option("--cdp-url", group="Browser options", default="", help="Connect to existing Chrome via CDP (e.g. http://localhost:9222 or wss://chrome.browserless.io?token=xxx). Mutually exclusive with --headless/--user-data-dir/--channel/--browser-arg.")
@_debug_options()
@click.pass_context
def browser(
    ctx: click.Context,
    instruction: str,
    name: str,
    model: str,
    thinking_level: str,
    media_resolution: str,
    language: str,
    max_steps: int,
    knowledge: str,
    output_format: str,
    url: str,
    headless: bool,
    viewport: str,
    channel: str,
    user_data_dir: str,
    browser_arg: tuple[str, ...],
    cdp_url: str,
    overlay: bool,
    report: bool,
    report_dir: str,
    annotate: bool,
    record: bool,
) -> None:
    """Run an AI task in a local browser."""
    if cdp_url and (headless or user_data_dir or channel or browser_arg):
        raise click.UsageError(
            "--cdp-url cannot be combined with --headless/--user-data-dir/--channel/--browser-arg"
        )
    vp = _parse_viewport(viewport)

    opts = _TaskOpts(
        ctx=ctx, instruction=instruction, name=name, model=model,
        thinking_level=thinking_level, media_resolution=media_resolution,
        language=language, max_steps=max_steps, knowledge=knowledge,
        output_format=output_format, overlay=overlay, report=report,
        report_dir=report_dir, annotate=annotate,
    )
    bot = _task_bot(opts, record=record)
    try:
        page = bot.open(
            url=url,
            headless=headless,
            viewport=vp,
            user_data_dir=user_data_dir,
            channel=channel,
            args=list(browser_arg) if browser_arg else None,
            cdp_url=cdp_url,
        )
        _run_local(
            bot, page, instruction, max_steps,
            knowledge=knowledge, output_format=output_format,
        )
    except Exception as e:
        # Only setup (bot.open) reaches here: _run_local reports its own errors
        # and exits via SystemExit, which this `except Exception` deliberately
        # skips. Record the setup failure so close() below doesn't complete the
        # task as succeeded.
        _fail_setup(bot, e, output_format)
    finally:
        bot.close()


def _flag_given(ctx: click.Context, param: str) -> bool:
    """True when the user explicitly passed the option (vs its default), so
    engine-specific flags can be rejected under the other engine without
    tripping on their own default values."""
    return ctx.get_parameter_source(param) == ParameterSource.COMMANDLINE


@dataclasses.dataclass
class _TaskOpts:
    """The options every task command shares (the @_task_options +
    @_debug_options set), bundled so the run helpers take one value instead
    of a 10-slot positional train that every new option would have to be
    threaded through in the right order at every call site."""

    ctx: click.Context
    instruction: str
    name: str
    model: str
    thinking_level: str
    media_resolution: str
    language: str
    max_steps: int
    knowledge: str
    output_format: str
    overlay: bool
    report: bool
    report_dir: str
    annotate: bool


def _task_bot(opts: _TaskOpts, **record_kwargs: Any) -> Any:
    """Build a task command's bot from the shared options, plus the
    command's recording flags; names the task after the instruction when
    -n was not given."""
    return _make_bot(
        opts.ctx, model=opts.model, thinking_level=opts.thinking_level,
        media_resolution=opts.media_resolution, language=opts.language,
        report=opts.report, report_dir=opts.report_dir, annotate=opts.annotate,
        task_name=opts.name or _default_task_name(opts.instruction),
        overlay=opts.overlay, output_format=opts.output_format,
        **record_kwargs,
    )


def _run_appium(opts: _TaskOpts, appium_url: str, options: Any, record: bool = False) -> None:
    """Shared android/ios body: build the bot, open an Appium session, run.

    ``record`` uses Appium's own screen-recording API (record_device), so it
    captures the device screen on both android and ios.
    """
    appium_webdriver = require("appium.webdriver", "appium")

    # Build the bot first: it validates credentials with a provider handshake and
    # may sys.exit() on failure. Creating the Appium driver before that would
    # leak the remote session (driver.quit() lives in the finally below, which
    # never runs if _make_bot exits before the try is entered).
    bot = _task_bot(opts, record=record, record_device=record)
    try:
        try:
            driver = appium_webdriver.Remote(appium_url, options=options)
        except Exception as e:
            # Appium setup failed before _run_local took over reporting; record
            # the failure so the outer finally:bot.close() doesn't complete the
            # task as succeeded. Scoped to Remote() only — a driver.quit() error
            # after a successful run must not be misreported as a task failure.
            _fail_setup(bot, e, opts.output_format)
        try:
            _run_local(
                bot, driver, opts.instruction, opts.max_steps,
                knowledge=opts.knowledge, output_format=opts.output_format,
            )
        finally:
            # The Appium recording lives in the session: flush it to disk
            # before quit() destroys it (bot.close() would be too late). A
            # no-op when nothing is recording.
            bot.stop_recording()
            driver.quit()
    finally:
        bot.close()


def _run_direct(
    opts: _TaskOpts,
    connect: Callable[[], Any],
    *,
    record: bool = False,
    record_mjpeg_url: str = "",
    record_device: bool = False,
    record_window: bool = False,
) -> None:
    """Shared direct-engine body: build the bot, connect the device, run.

    ``connect`` resolves the device + optional app launch and returns the bind
    target. Like _run_appium, the bot is built first (it validates the
    credential setup and may sys.exit()); there is no remote session to quit,
    so the only teardown is bot.close(). Recording is device-side for
    android/ios — ``record_mjpeg_url`` for ios (WDA's MJPEG stream),
    ``record_device`` for android (adb screenrecord, resolved from the
    AdbDevice target) — and host-side for desktop, where ``record_window``
    makes it follow the bound window instead of grabbing the full screen.
    """
    bot = _task_bot(
        opts, record=record, record_mjpeg_url=record_mjpeg_url,
        record_device=record_device, record_window=record_window,
    )
    try:
        try:
            target = connect()
        except Exception as e:
            # Same contract as _run_appium: a setup failure before _run_local
            # takes over reporting must be recorded, or the finally:bot.close()
            # would complete the task as succeeded.
            _fail_setup(bot, e, opts.output_format)
        _run_local(
            bot, target, opts.instruction, opts.max_steps,
            knowledge=opts.knowledge, output_format=opts.output_format,
        )
    finally:
        bot.close()


def _adb_launch_app(dev: Any, package: str, activity: str) -> None:
    """Launch an app over adb: explicit activity via ``am start -W``, else the
    LAUNCHER intent via monkey (no need to know the activity name)."""
    if activity:
        component = f"{package}/{activity}"
        out = dev.shell(f"am start -W -n {component}")
        if "Error" in out or "does not exist" in out:
            raise RuntimeError(
                f"could not launch {component}: {out.strip().splitlines()[-1]}"
            )
    else:
        out = dev.shell(
            f"monkey -p {package} -c android.intent.category.LAUNCHER 1"
        )
        if "No activities found" in out or "aborted" in out.lower():
            raise RuntimeError(
                f"could not launch {package}: no LAUNCHER activity found "
                "(is the package name right? try --app-activity)"
            )


@cli.command(cls=_GroupedCommand)
@click.argument("instruction")
@_task_options
@_option("--device", "-d", group="Android options", default="", help="Which device: an adb serial from `adb devices` (e.g. emulator-5554 or 192.168.1.8:5555). Optional when exactly one device is connected. With --appium-url: passed as deviceName.")
@_option("--appium-url", group="Android options", default="http://localhost:4723", help="Appium server URL — passing this flag switches the run to the Appium engine", show_default="direct adb, no server")
# Android — app launch
@_option("--app-package", group="Android options", default="", help="App package to launch (e.g. com.android.settings)")
@_option("--app-activity", group="Android options", default="", help="App activity to launch")
# Android — device-screen recording: adb screenrecord (direct engine) or
# Appium's recording API — both capture the phone screen, not the host's.
@_option("--record", group="Android options", is_flag=True, help="Record the device screen to report-dir/recording.mp4 (direct engine: adb screenrecord, ffmpeg merges runs over 3 min; Appium engine: Appium's recording API)")
@_debug_options(record=False)
@click.pass_context
def android(ctx: click.Context, instruction: str, name: str, model: str, thinking_level: str, media_resolution: str, language: str, max_steps: int, knowledge: str, output_format: str, device: str, appium_url: str, app_package: str, app_activity: str, record: bool, overlay: bool, report: bool, report_dir: str, annotate: bool) -> None:
    """Run an AI task on an Android device (direct over adb; --appium-url for Appium).

    \b
    Default — drives the device straight over adb. Zero Python dependencies;
    the only requirement is an adb binary (platform-tools) on PATH:
      qirabot android "Open settings"                    # the only adb device
      qirabot android "..." -d emulator-5554             # pick one of several
      qirabot android "..." -d 192.168.1.8:5555          # network device (adb connect)
      qirabot android "..." --app-package com.android.settings --app-activity .Settings
    \b
    Appium — passing --appium-url selects the Appium engine; needs a running
    server (npm i -g appium && appium driver install uiautomator2 && appium):
      qirabot android "..." --appium-url http://localhost:4723 -d emulator-5554
    \b
    Recording — --record saves the device screen (works on both engines):
      qirabot android "..." --record
    """
    opts = _TaskOpts(
        ctx=ctx, instruction=instruction, name=name, model=model,
        thinking_level=thinking_level, media_resolution=media_resolution,
        language=language, max_steps=max_steps, knowledge=knowledge,
        output_format=output_format, overlay=overlay, report=report,
        report_dir=report_dir, annotate=annotate,
    )
    if _flag_given(ctx, "appium_url"):
        require("appium.webdriver", "appium")
        from appium.options.android import UiAutomator2Options

        options = UiAutomator2Options()
        if device:
            options.device_name = device
        if app_package:
            options.app_package = app_package
        if app_activity:
            options.app_activity = app_activity

        _run_appium(opts, appium_url, options, record=record)
        return

    from qirabot.adb import AdbDevice

    dev = AdbDevice(serial=device or None)

    def connect() -> Any:
        # Resolve the serial now so 0/many/unauthorized/offline devices fail
        # with their actionable errors inside _fail_setup's reporting.
        dev.serial  # noqa: B018 — resolution side effect
        if app_package:
            _adb_launch_app(dev, app_package, app_activity)
        return dev

    _run_direct(opts, connect, record=record, record_device=record)


def _check_wda_ready(client: Any, wda_url: str) -> None:
    """Fail fast, with the full fix, when WebDriverAgent isn't answering."""
    if client.is_ready():
        return
    raise RuntimeError(
        f"WDA is not running (nothing answered at {wda_url}); start "
        "WebDriverAgent first (USB real device: `iproxy 8100 8100` alongside "
        "it; Xcode: run the WebDriverAgentRunner test scheme on the device, "
        "or `tidevice3 runwda` / pymobiledevice3), then retry — or pass "
        "--appium-url to have Appium build and launch WDA"
    )


def _wda_mjpeg_url(wda_url: str) -> str:
    """Default WDA MJPEG stream URL for ``wda_url``: same host, port 9100.

    9100 is WDA's default mjpegServerPort; like 8100, a USB real device needs
    its own forward (`iproxy 9100 9100`).
    """
    from urllib.parse import urlsplit

    url = wda_url if wda_url.startswith("http") else f"http://{wda_url}"
    host = urlsplit(url).hostname or "127.0.0.1"
    return f"http://{host}:9100"


def _check_mjpeg_ready(mjpeg_url: str) -> None:
    """Fail fast when --record was asked for but the MJPEG stream isn't up.

    Recording is the one thing that can't be salvaged after the fact — a
    silent best-effort skip would only be discovered after a full (possibly
    300-step) run. Probe before the task is even created and exit with the
    fix instead.
    """
    from qirabot.recording import check_mjpeg_stream

    err = check_mjpeg_stream(mjpeg_url)
    if err:
        raise click.ClickException(
            f"{err}. WDA streams the device screen on port 9100 — USB real "
            "device: run `iproxy 9100 9100` (alongside the usual 8100 forward) "
            "and retry, or point --mjpeg-url at the stream."
        )


@cli.command(cls=_GroupedCommand)
@click.argument("instruction")
@_task_options
@_option("--wda-url", group="iOS options", default="http://127.0.0.1:8100", help="WebDriverAgent URL — this is how the default engine picks the device (USB real device: run `iproxy 8100 8100` and keep the default; another device = its WDA address)")
# No -d short here, unlike android: on ios --device switches the engine to
# Appium, and an engine switch should be typed out deliberately, not inherited
# as muscle memory from `qirabot android -d`.
@_option("--device", group="iOS options", default="", help="Simulator device type (a name from `xcrun simctl list devicetypes`, e.g. \"iPhone 15\") — passing this flag switches the run to the Appium engine (simulators only). Real devices: keep the default engine, which selects the device via --wda-url.")
@_option("--appium-url", group="iOS options", default="http://localhost:4723", help="Appium server URL — passing this flag switches the run to the Appium engine", show_default="direct WDA, no server")
# iOS — app launch
@_option("--bundle-id", group="iOS options", default="", help="App bundle id to launch (e.g. com.tencent.xin)")
# iOS — device-screen recording: the default engine transcodes WDA's MJPEG
# stream with ffmpeg; the Appium engine uses Appium's recording API. Either
# way this captures the phone screen, unlike the desktop --record.
@_option("--record", group="iOS options", is_flag=True, help="Record the device screen to report-dir/recording.mp4 (default engine: WDA's MJPEG stream, requires ffmpeg, USB real device also needs `iproxy 9100 9100`; Appium engine: Appium's recording API)")
@_option("--mjpeg-url", group="iOS options", default="", help="WDA MJPEG stream URL for --record (default: --wda-url's host on port 9100; direct engine only)")
@_debug_options(record=False)
@click.pass_context
def ios(ctx: click.Context, instruction: str, name: str, model: str, thinking_level: str, media_resolution: str, language: str, max_steps: int, knowledge: str, output_format: str, wda_url: str, device: str, appium_url: str, bundle_id: str, record: bool, mjpeg_url: str, overlay: bool, report: bool, report_dir: str, annotate: bool) -> None:
    """Run an AI task on an iOS device (direct via WDA; --appium-url/--device for Appium).

    \b
    Default — talks to WebDriverAgent directly (built in, zero extra installs).
    WDA must be running on the device (USB real device: `iproxy 8100 8100`):
      qirabot ios "..." --bundle-id com.tencent.xin          # WDA on 127.0.0.1:8100
      qirabot ios "..." --wda-url http://192.168.1.20:8100   # another device's WDA
    \b
    Appium — passing --appium-url or --device (simulator) selects the Appium
    engine; needs a running server (npm i -g appium && appium driver install
    xcuitest && appium), and can auto build/sign WDA for you:
      qirabot ios "..." --device "iPhone 15" --bundle-id com.apple.Preferences
    \b
    Recording — --record saves the device screen. Default engine: WDA's MJPEG
    stream (port 9100; USB real device: also `iproxy 9100 9100`). Appium
    engine: Appium's own recording API, no extra setup:
      qirabot ios "..." --record
    """
    opts = _TaskOpts(
        ctx=ctx, instruction=instruction, name=name, model=model,
        thinking_level=thinking_level, media_resolution=media_resolution,
        language=language, max_steps=max_steps, knowledge=knowledge,
        output_format=output_format, overlay=overlay, report=report,
        report_dir=report_dir, annotate=annotate,
    )
    if _flag_given(ctx, "appium_url") or _flag_given(ctx, "device"):
        if _flag_given(ctx, "wda_url"):
            raise click.UsageError("--wda-url only applies to the direct engine (drop --appium-url/--device)")
        if _flag_given(ctx, "mjpeg_url"):
            raise click.UsageError("--mjpeg-url only applies to the direct engine (the Appium engine records via Appium's own API)")
        require("appium.webdriver", "appium")
        from appium.options.ios import XCUITestOptions

        options = XCUITestOptions()
        if device:
            options.device_name = device
        if bundle_id:
            options.bundle_id = bundle_id

        _run_appium(opts, appium_url, options, record=record)
        return

    if _flag_given(ctx, "mjpeg_url") and not record:
        raise click.UsageError("--mjpeg-url only applies with --record")
    record_mjpeg_url = ""
    if record:
        record_mjpeg_url = mjpeg_url or _wda_mjpeg_url(wda_url)
        _check_mjpeg_ready(record_mjpeg_url)

    from qirabot.wda import WdaClient

    client = WdaClient(wda_url)

    def connect() -> Any:
        _check_wda_ready(client, wda_url)
        if bundle_id:
            client.app_launch(bundle_id)
        return client

    _run_direct(opts, connect, record=record, record_mjpeg_url=record_mjpeg_url)


def _launch_desktop_app(app: str, app_wait: float) -> None:
    """--app side effect shared by both desktop engines; exits 1 on failure."""
    from qirabot import launch_app

    try:
        launch_app(app, wait=app_wait)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


@cli.command(cls=_GroupedCommand)
@click.argument("instruction")
@_task_options
@_option("--window-title", group="Desktop options", default="", help="Bind to the window whose title matches this regex — selects the built-in Windows window backend (screenshots/coords become window-relative, input is game-readable scancodes, recording follows the window). Windows only.")
@_option("--hwnd", group="Desktop options", default=0, type=int, help="Bind to a specific window handle — selects the built-in Windows window backend. Windows only.")
@_option("--ambiguous", group="Desktop options", default="error", type=click.Choice(["error", "largest"]), help="What to do when several windows match --window-title: 'error' fails listing the candidates; 'largest' picks the biggest matching window — for games whose launcher/overlay windows share the main window's title.")
# Desktop — app launch
@_option("--app", group="Desktop options", default="", help="Launch/activate an app before the task. macOS: app name (\"WeChat\") or bundle id; Windows: exe path, registered name, or UWP AppUserModelID; Linux: executable.")
@_option("--app-wait", group="Desktop options", default=2.0, type=float, help="Seconds to wait after --app launch for the window to appear")
@_debug_options()
@click.pass_context
def desktop(ctx: click.Context, instruction: str, name: str, model: str, thinking_level: str, media_resolution: str, language: str, max_steps: int, knowledge: str, output_format: str, window_title: str, hwnd: int, ambiguous: str, app: str, app_wait: float, overlay: bool, report: bool, report_dir: str, annotate: bool, record: bool) -> None:
    """Run an AI task on the desktop (pyautogui; --window-title/--hwnd for one Windows window).

    \b
    Default — pyautogui, drives the whole screen (macOS/Windows/Linux):
      qirabot desktop "Create a note titled Groceries" --app Notes
    \b
    Windows window backend (built in, zero extra installs) — passing
    --window-title or --hwnd binds to one window: screenshots and clicks are
    window-relative, and keys go out as DirectInput scancodes that games can
    read (virtual-key input often can't reach them):
      qirabot desktop "..." --window-title "Genshin"
      qirabot desktop "..." --app "C:/game.exe" --app-wait 15 --window-title "..."
    """
    if ambiguous != "error" and not window_title:
        raise click.UsageError(
            "--ambiguous largest only applies with --window-title "
            "(--hwnd already names one window)"
        )
    opts = _TaskOpts(
        ctx=ctx, instruction=instruction, name=name, model=model,
        thinking_level=thinking_level, media_resolution=media_resolution,
        language=language, max_steps=max_steps, knowledge=knowledge,
        output_format=output_format, overlay=overlay, report=report,
        report_dir=report_dir, annotate=annotate,
    )
    if window_title or hwnd:
        if window_title and hwnd:
            raise click.UsageError("--window-title and --hwnd are mutually exclusive")
        if sys.platform != "win32":
            # macOS's CGEvent has no concept of delivering input to a
            # background window, so a real window-bound backend cannot exist
            # there — fail with the workable alternative, don't degrade.
            raise click.UsageError(
                "--window-title/--hwnd need the Windows window backend, which "
                "only exists on Windows; on macOS/Linux use the default "
                "full-screen mode and bring the target window to the front"
            )
        from qirabot.windows import Window

        window = Window(
            hwnd=hwnd or None, title_re=window_title or None, ambiguous=ambiguous,
        )

        def connect() -> Any:
            # Launch the app only after _run_direct has built the bot: a bad
            # credential setup must error out before the --app side effect,
            # and a launch failure lands in _fail_setup's reporting.
            if app:
                _launch_desktop_app(app, app_wait)
            window.hwnd  # noqa: B018 — resolve now for actionable errors
            return window

        # record_window unconditionally: it only takes effect when a recording
        # actually starts (--record here, or QIRA_RECORD=1 from the env), and
        # then makes it follow the bound window instead of the full screen.
        _run_direct(opts, connect, record=record, record_window=True)
        return

    pyautogui = require("pyautogui", "desktop")

    def connect_pyautogui() -> Any:
        # Same ordering contract as the window path's connect(): the bot is
        # built first (a bad credential setup must not launch the app and
        # only then error out), and an --app launch failure lands in
        # _fail_setup's reporting.
        if app:
            _launch_desktop_app(app, app_wait)
        return pyautogui

    _run_direct(opts, connect_pyautogui, record=record)


def main() -> None:
    # The SDK never reads .env implicitly; the CLI is the "calling script" in
    # that contract, so it opts in here — before click parses options, so the
    # envvar fallbacks (QIRA_MODEL, QIRA_VERTEX_PROJECT,
    # GOOGLE_APPLICATION_CREDENTIALS, ...) see the values. Best-effort, and
    # exported variables always win over .env entries.
    load_dotenv()
    # Catch SDK errors that escape command bodies and print them as one line,
    # no traceback (e.g. MissingDependencyError's install hint, which may
    # surface deep inside a command via lazy imports).
    try:
        cli()
    except QirabotError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
