"""Executable bootstrap for the Agent UI interactive and one-shot CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from a13n_logging import LogFormat, configure_logging

from a13n_ui.errors import AgentUiError
from a13n_ui.host import AgentUiHost, open_agent_ui_host
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings

OutputFormat = Literal["text", "json"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a13n-ui")
    parser.add_argument(
        "--config",
        type=Path,
        help="explicit Agent UI settings YAML (default: ~/.a13n-ui/settings.yaml)",
    )
    commands = parser.add_subparsers(dest="command")
    runtime = commands.add_parser("runtime", help="inspect or restart the runtime Runner")
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
            output: OutputFormat = "json" if args.json else "text"
            status = await host.runtime_status()
            _print_runtime_status(status.model_dump(mode="json"), output=output)
            return
        await _interactive(host)


async def _interactive(host: AgentUiHost) -> None:
    print("Agent UI CLI. Commands: /runtime, /restart, /exit")
    while True:
        try:
            line = (await asyncio.to_thread(input, "a13n-ui> ")).strip()
        except EOFError:
            print()
            return
        if line in {"/exit", "/quit"}:
            return
        if line == "/runtime":
            status = await host.runtime_status()
            _print_runtime_status(status.model_dump(mode="json"), output="text")
        elif line == "/restart":
            result = await host.restart_runtime()
            print(f"active {result.active.generation_id} ({result.active.state.value})")
        elif line:
            print("Session interaction is not available in this runtime-management slice.")


def _print_runtime_status(value: dict[str, object], *, output: OutputFormat) -> None:
    if output == "json":
        print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        return
    active = value.get("active_generation_id")
    generations = value.get("generations")
    print(f"active {active or 'unavailable'}")
    if isinstance(generations, list):
        for item in generations:
            if isinstance(item, dict):
                generation_id = item.get("generation_id", "unknown")
                state = item.get("state", "unknown")
                process_id = item.get("process_id", "-")
                print(f"  {generation_id} {state} pid={process_id}")


__all__ = ["main"]
