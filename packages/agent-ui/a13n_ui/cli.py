"""Executable bootstrap for the Agent UI interactive and one-shot CLI."""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence
from pathlib import Path

from a13n_logging import LogFormat, configure_logging

from a13n_ui.errors import AgentUiError
from a13n_ui.host import open_agent_ui_host
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings
from a13n_ui.tui import print_runtime_status
from a13n_ui.tui import run as run_tui


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a13n-ui")
    parser.add_argument(
        "--config",
        type=Path,
        help="explicit Agent UI settings YAML (default: ~/.a13n-ui/settings.yaml)",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("tui", help="run the interactive terminal frontend")
    runtime = commands.add_parser("runtime", help="inspect the runtime Runner")
    runtime_commands = runtime.add_subparsers(dest="runtime_command", required=True)
    status = runtime_commands.add_parser("status")
    status.add_argument("--json", action="store_true", help="emit one JSON document")
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        asyncio.run(_run(args))
    except KeyboardInterrupt:
        pass
    except AgentUiError as exc:
        print(f"{exc.code}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


async def _run(args: argparse.Namespace) -> None:
    source = await load_agent_ui_settings(args.config)
    await asyncio.to_thread(ensure_default_directories, source)
    settings = source.settings
    configure_logging(
        level=settings.log_level,
        log_format=LogFormat(settings.log_format),
        logger_names=("a13n_ui",),
    )
    async with open_agent_ui_host(settings) as host:
        if args.command == "runtime":
            output = "json" if args.json else "text"
            status = await host.runtime_status()
            print_runtime_status(status.model_dump(mode="json"), output=output)
            return
        await run_tui(host)


__all__ = ["main"]
