"""Executable bootstrap for the Agent UI interactive and one-shot CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from a13n_harness.model_auth import CodexCredentials, CodexOAuthFlow
from a13n_logging import LogFormat, configure_logging
from pydantic import BaseModel

from a13n_ui.app import AgentUiApp, open_agent_ui_app
from a13n_ui.configuration import (
    ExternalSubagentProduct,
    ExternalSubagentScope,
    LoadedAgentUiConfiguration,
)
from a13n_ui.errors import AgentUiError, ConfigurationError
from a13n_ui.model_accounts import Provider
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings
from a13n_ui.terminal import run as run_cli
from a13n_ui.thread_service import RootRunOutcome, RootThreadDefaults

_MAX_OUTPUT_BYTES = 256 * 1024
_TRUNCATION_MARKER = "\n...[output truncated]\n"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="a13n-ui")
    parser.add_argument(
        "--config",
        type=Path,
        help="explicit Agent UI configuration YAML (default: ~/.a13n-ui/a13n-ui.yaml)",
    )
    parser.add_argument(
        "--data-root",
        type=Path,
        help="override the local Agent UI data root",
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("cli", help="run the interactive terminal frontend")
    commands.add_parser("web", help="run the bundled WebUI frontend")

    run = commands.add_parser("run", help="execute one headless Thread message")
    run.add_argument("prompt", help="message to execute")
    run.add_argument("--thread", help="continue an existing root Thread")
    run.add_argument("--project", help="Project used when creating a Thread")
    run.add_argument("--agent", help="Agent used when creating a Thread")
    run.add_argument(
        "--environment-profile",
        help="Environment profile used when creating a Thread",
    )
    run.add_argument("--title", help="title used when creating a Thread")
    _add_format(run)

    config = commands.add_parser("config", help="validate, inspect, or import configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    _add_format(config_commands.add_parser("validate", help="validate the selected source tree"))
    _add_format(config_commands.add_parser("show", help="show the accepted configuration"))
    import_subagents = config_commands.add_parser("import-subagents", help="preview or apply external subagent imports")
    import_subagents.add_argument(
        "--product",
        choices=tuple(item.value for item in ExternalSubagentProduct),
        required=True,
    )
    import_subagents.add_argument(
        "--scope",
        choices=tuple(item.value for item in ExternalSubagentScope),
        required=True,
    )
    import_subagents.add_argument("--project-root", type=Path)
    import_subagents.add_argument("--user-home", type=Path)
    import_subagents.add_argument(
        "--apply",
        action="store_true",
        help="apply every ready candidate; omission is a dry-run preview",
    )
    _add_format(import_subagents)

    project = commands.add_parser("project", help="query configured Projects")
    project_commands = project.add_subparsers(dest="project_command", required=True)
    project_list = project_commands.add_parser("list", help="list Projects")
    _add_format(project_list)

    thread = commands.add_parser("thread", help="query or archive Threads")
    thread_commands = thread.add_subparsers(dest="thread_command", required=True)
    thread_list = thread_commands.add_parser("list", help="list Threads")
    thread_list.add_argument("--query")
    thread_list.add_argument("--include-archived", action="store_true")
    thread_list.add_argument("--offset", type=int, default=0)
    thread_list.add_argument("--limit", type=int, default=20)
    _add_format(thread_list)
    thread_show = thread_commands.add_parser("show", help="inspect one Thread")
    thread_show.add_argument("thread_id")
    thread_show.add_argument("--history-offset", type=int, default=0)
    thread_show.add_argument("--history-limit", type=int, default=50)
    _add_format(thread_show)
    thread_archive = thread_commands.add_parser("archive", help="archive one Thread")
    thread_archive.add_argument("thread_id")
    thread_archive.add_argument("--restore", action="store_true")
    _add_format(thread_archive)

    doctor = commands.add_parser("doctor", help="inspect App and extension health")
    _add_format(doctor)

    account = commands.add_parser("account", help="manage compatible Model accounts")
    account_commands = account.add_subparsers(dest="account_command", required=True)
    for name in ("status", "logout"):
        account_command = account_commands.add_parser(name)
        account_command.add_argument("provider", choices=tuple(item.value for item in Provider))
        _add_format(account_command)
    account_login = account_commands.add_parser("login")
    account_login.add_argument("provider", choices=(Provider.CODEX.value,))
    account_login.add_argument("--allow-account-switch", action="store_true")
    _add_format(account_login)
    return parser


def _add_format(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="terminal output format (default: text)",
    )


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
    source = await load_agent_ui_settings(args.config, data_root=args.data_root)
    await asyncio.to_thread(ensure_default_directories, source)
    settings = source.settings
    configure_logging(
        level=settings.log_level,
        log_format=LogFormat(settings.log_format),
        logger_names=("a13n_ui",),
    )
    if args.command == "web":
        from a13n_ui.webui import run as run_web

        await asyncio.to_thread(run_web)
        return 0

    async with open_agent_ui_app(
        settings,
        configuration_path=source.path,
        configuration_error=source.candidate_error,
        codex_login=_codex_cli_login,
    ) as app:
        if args.command == "run":
            configuration = await app.current_configuration()
            if configuration is None:
                raise ConfigurationError(
                    "No accepted Agent UI configuration is available.",
                    code="configuration_unavailable",
                )
            return await _run_one_shot(app, configuration, args)
        if args.command in {"config", "project", "thread", "doctor", "account"}:
            return await _run_management(app, args)
        await run_cli(app)
    return 0


async def _codex_cli_login(request: object) -> CodexCredentials:
    del request
    flow = CodexOAuthFlow()
    print(f"Open this URL to authenticate Codex:\n{flow.authorization_url()}", file=sys.stderr)
    return await flow.exchange_code_from_callback()


async def _run_management(app: AgentUiApp, args: argparse.Namespace) -> int:
    if args.command == "config":
        if args.config_command == "validate":
            status = await app.status()
            projection = {
                "valid": status.candidate_error_code is None,
                "accepted_generation_digest": status.accepted_generation_digest,
                "error": (
                    None
                    if status.candidate_error_code is None
                    else {
                        "code": status.candidate_error_code,
                        "message": status.candidate_error_message,
                    }
                ),
            }
            _print_projection(projection, args.format)
            return 0 if projection["valid"] else 1
        if args.config_command == "show":
            configuration = await _require_configuration(app)
            _print_projection(configuration.model_dump(mode="json"), args.format)
            return 0
        preview = await app.preview_subagent_import(
            product=args.product,
            scope=args.scope,
            project_root=args.project_root,
            user_home=args.user_home,
        )
        applied: list[object] = []
        if args.apply:
            for candidate in preview.candidates:
                if candidate.status == "ready":
                    applied.append(await app.apply_subagent_import(candidate))
        _print_projection(
            {
                "dry_run": not args.apply,
                "preview": preview,
                "applied": applied,
            },
            args.format,
        )
        return 0 if all(item.status != "invalid" for item in preview.candidates) else 1

    if args.command == "project":
        _print_projection({"projects": await app.projects()}, args.format)
        return 0

    if args.command == "thread":
        if args.thread_command == "list":
            threads, total = await app.list_threads(
                query=args.query,
                include_archived=args.include_archived,
                offset=args.offset,
                limit=args.limit,
            )
            _print_projection(
                {"threads": threads, "offset": args.offset, "total": total},
                args.format,
            )
            return 0
        if args.thread_command == "show":
            thread, history, total = await app.inspect_thread(
                thread_id=args.thread_id,
                history_offset=args.history_offset,
                history_limit=args.history_limit,
            )
            _print_projection(
                {"thread": thread, "history": history, "history_total": total},
                args.format,
            )
            return 0
        thread = await app.archive_thread(
            thread_id=args.thread_id,
            archived=not args.restore,
        )
        _print_projection(thread, args.format)
        return 0

    if args.command == "doctor":
        status = await app.status()
        references = await app.list_catalog()
        projection = {
            "status": status,
            "catalog": {
                "total": len(references),
                "ambiguous": [item for item in references if not item.configurable],
            },
        }
        _print_projection(projection, args.format)
        return 0 if status.candidate_error_code is None else 1

    provider = Provider(args.provider)
    if args.account_command == "status":
        result: object = await app.inspect_model_account(provider)
    elif args.account_command == "login":
        result = await app.login_model_account(
            provider,
            allow_account_switch=args.allow_account_switch,
        )
    else:
        result = {"provider": provider.value, "logged_out": await app.logout_model_account(provider)}
    _print_projection(result, args.format)
    return 0


async def _require_configuration(app: AgentUiApp) -> LoadedAgentUiConfiguration:
    configuration = await app.current_configuration()
    if configuration is None:
        raise ConfigurationError(
            "No accepted Agent UI configuration is available.",
            code="configuration_unavailable",
        )
    return configuration


async def _run_one_shot(
    app: AgentUiApp,
    configuration: LoadedAgentUiConfiguration,
    args: argparse.Namespace,
) -> int:
    thread_id = args.thread
    if thread_id is not None:
        if any(
            value is not None
            for value in (
                args.project,
                args.agent,
                args.environment_profile,
                args.title,
            )
        ):
            raise ConfigurationError(
                "Thread creation options cannot be used with --thread.",
                code="run_arguments_conflict",
            )
    else:
        defaults = _new_thread_defaults(configuration, args)
        thread = await app.create_thread(defaults=defaults, title=args.title)
        thread_id = thread.thread_id

    outcome = await app.run_thread(thread_id=thread_id, prompt=args.prompt)
    projection = _run_projection(thread_id, outcome)
    if args.format == "json":
        print(json.dumps(projection, ensure_ascii=False, separators=(",", ":")))
    else:
        _print_text_result(projection)
    return 0 if outcome.result.status == "completed" and outcome.continuation.status == "selected" else 1


def _new_thread_defaults(
    configuration: LoadedAgentUiConfiguration,
    args: argparse.Namespace,
) -> RootThreadDefaults:
    defaults = configuration.document.defaults
    project_id = args.project or defaults.project
    agent_id = args.agent or defaults.agent
    if project_id is None:
        raise ConfigurationError(
            "A headless Run requires --project or defaults.project.",
            code="run_project_required",
        )
    if project_id not in configuration.projects:
        raise ConfigurationError(
            "The selected headless Run Project does not exist.",
            code="run_project_missing",
            details={"project_id": project_id},
        )
    if agent_id is None:
        raise ConfigurationError(
            "A headless Run requires --agent or defaults.agent.",
            code="run_agent_required",
        )
    if agent_id not in configuration.agents:
        raise ConfigurationError(
            "The selected headless Run Agent does not exist.",
            code="run_agent_missing",
            details={"agent_id": agent_id},
        )
    return RootThreadDefaults(
        project_id=project_id,
        agent_id=agent_id,
        environment_profile_id=(args.environment_profile or defaults.environment_profile),
    )


def _run_projection(thread_id: str, outcome: RootRunOutcome) -> dict[str, Any]:
    result = outcome.result
    output, output_truncated = _bounded_text(result.output if isinstance(result.output, str) else None)
    failure = result.failure
    continuation_ref = outcome.continuation.reference
    publications = Counter(item.status for item in outcome.environment.state_publications)
    return {
        "thread_id": thread_id,
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
        "composition": outcome.composition.model_dump(mode="json"),
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
                f"Thread continuation was not selected: {continuation['status']}.",
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


def _print_projection(value: object, output_format: str) -> None:
    payload = _jsonable(value)
    if output_format == "json":
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _jsonable(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


__all__ = ["main"]
