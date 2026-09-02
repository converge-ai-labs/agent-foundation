"""Executable bootstrap for the Agent UI interactive and one-shot CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from a13n_logging import LogFormat, configure_logging

from a13n_ui.app import AgentUiApp, open_agent_ui_app
from a13n_ui.composition import IMPLICIT_NATIVE_PROFILE
from a13n_ui.configuration import LoadedAgentUiConfiguration
from a13n_ui.errors import AgentUiError, ConfigurationError
from a13n_ui.session_service import RootRunOutcome
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings
from a13n_ui.terminal import run as run_cli

_MAX_OUTPUT_BYTES = 256 * 1024
_TRUNCATION_MARKER = "\n...[output truncated]\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a13n-ui")
    parser.add_argument(
        "--config",
        type=Path,
        help="explicit Agent UI settings YAML (default: ~/.a13n-ui/a13n-ui.yaml)",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("cli", help="run the interactive terminal frontend")
    run = commands.add_parser("run", help="execute one headless Session message")
    run.add_argument("prompt", help="message to execute")
    run.add_argument("--session", help="continue an existing Session")
    run.add_argument("--agent", help="Agent used when creating a Session")
    run.add_argument("--environment", help="Environment profile used when creating a Session")
    run.add_argument("--title", help="title used when creating a Session")
    run.add_argument(
        "--folder",
        action="append",
        type=Path,
        help="bound workspace folder; repeat for multiple folders (default: current directory)",
    )
    run.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="terminal output format (default: text)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    try:
        exit_code = asyncio.run(_run(args))
    except KeyboardInterrupt:
        return
    except AgentUiError as exc:
        if getattr(args, "format", "text") == "json":
            print(json.dumps({"error": {"code": exc.code, "message": str(exc)}}))
        else:
            print(f"{exc.code}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    if exit_code:
        raise SystemExit(exit_code)


async def _run(args: argparse.Namespace) -> int:
    source = await load_agent_ui_settings(args.config)
    await asyncio.to_thread(ensure_default_directories, source)
    settings = source.settings
    configure_logging(
        level=settings.log_level,
        log_format=LogFormat(settings.log_format),
        logger_names=("a13n_ui",),
    )
    async with open_agent_ui_app(settings, configuration=source.configuration) as app:
        if args.command == "run":
            return await _run_one_shot(app, source.configuration, args)
        await run_cli(app)
    return 0


async def _run_one_shot(
    app: AgentUiApp,
    configuration: LoadedAgentUiConfiguration,
    args: argparse.Namespace,
) -> int:
    session_id = args.session
    if session_id is not None:
        if args.agent is not None or args.environment is not None or args.title is not None:
            raise ConfigurationError(
                "--agent, --environment, and --title cannot be used with --session.",
                code="run_arguments_conflict",
            )
    else:
        agent_name, environment_name = _new_session_selection(configuration, args)
        session = await app.create_session(
            agent_name=agent_name,
            environment_name=environment_name,
            title=args.title,
        )
        session_id = session.session_id

    folders = tuple(args.folder) if args.folder else (Path.cwd(),)
    outcome = await app.run_session(
        session_id=session_id,
        prompt=args.prompt,
        folders=folders,
    )
    projection = _run_projection(session_id, outcome)
    if args.format == "json":
        print(json.dumps(projection, ensure_ascii=False, separators=(",", ":")))
    else:
        _print_text_result(projection)
    return 0 if outcome.result.status == "completed" and outcome.continuation.status == "selected" else 1


def _new_session_selection(
    configuration: LoadedAgentUiConfiguration,
    args: argparse.Namespace,
) -> tuple[str, str]:
    document = configuration.document
    agent_name = args.agent or document.defaults.agent
    if agent_name is None:
        raise ConfigurationError(
            "A headless Run requires --agent or defaults.agent.",
            code="run_agent_required",
        )
    agent = document.agents.get(agent_name)
    if agent is None:
        raise ConfigurationError(
            "The selected headless Run Agent does not exist.",
            code="run_agent_missing",
            details={"agent_name": agent_name},
        )
    environment_name = args.environment or agent.environment or document.defaults.environment or IMPLICIT_NATIVE_PROFILE
    return agent_name, environment_name


def _run_projection(session_id: str, outcome: RootRunOutcome) -> dict[str, Any]:
    result = outcome.result
    output, output_truncated = _bounded_text(result.output if isinstance(result.output, str) else None)
    failure = result.failure
    continuation_ref = outcome.continuation.reference
    publications = Counter(item.status for item in outcome.environment.state_publications)
    return {
        "session_id": session_id,
        "thread_id": result.thread_id,
        "run_id": result.run_id,
        "status": result.status,
        "output": output,
        "output_truncated": output_truncated,
        "failure": (
            None
            if failure is None
            else {
                "code": failure.code,
                "message": _bounded_text(failure.message)[0],
                "retry_hint": failure.retry_hint,
            }
        ),
        "suspend_reason": result.suspend_reason,
        "continuation": {
            "status": outcome.continuation.status,
            "reference": (continuation_ref.model_dump(mode="json") if continuation_ref is not None else None),
        },
        "environment": {
            "cleanup_error_count": len(outcome.environment.cleanup_errors),
            "state_publications": dict(sorted(publications.items())),
        },
    }


def _print_text_result(projection: dict[str, Any]) -> None:
    status = projection["status"]
    if status == "completed":
        print(projection["output"] or "")
        continuation = projection["continuation"]
        if isinstance(continuation, dict) and continuation["status"] != "selected":
            print(
                f"Session continuation was not selected: {continuation['status']}.",
                file=sys.stderr,
            )
        environment = projection["environment"]
        if isinstance(environment, dict) and environment["cleanup_error_count"]:
            print(
                f"Environment cleanup reported {environment['cleanup_error_count']} error(s).",
                file=sys.stderr,
            )
        return
    failure = projection["failure"]
    if isinstance(failure, dict):
        print(f"{failure['code']}: {failure['message']}", file=sys.stderr)
    elif status == "suspended":
        print("Run suspended with deferred tool requests.", file=sys.stderr)
    else:
        print(f"Run {status}.", file=sys.stderr)


def _bounded_text(value: str | None) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    encoded = value.encode("utf-8")
    if len(encoded) <= _MAX_OUTPUT_BYTES:
        return value, False
    budget = _MAX_OUTPUT_BYTES - len(_TRUNCATION_MARKER.encode("utf-8"))
    prefix = encoded[:budget].decode("utf-8", errors="ignore")
    return prefix + _TRUNCATION_MARKER, True


__all__ = ["main"]
