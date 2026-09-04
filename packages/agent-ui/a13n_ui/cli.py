"""Executable bootstrap for the Agent UI interactive and one-shot CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from a13n_harness.model_auth import (
    CodexCredentials,
    CodexOAuthFlow,
    GrokCredentials,
    GrokDeviceAuthorizationFlow,
    GrokOAuthFlow,
)
from a13n_logging import LogFormat, configure_logging
from pydantic import BaseModel

from a13n_ui.app import AgentUiApp, open_agent_ui_app
from a13n_ui.configuration import (
    ExternalSubagentProduct,
    ExternalSubagentScope,
    LoadedAgentUiConfiguration,
)
from a13n_ui.environment_profiles import EnvironmentMode, environment_profile_id_for_mode
from a13n_ui.errors import AgentUiError, ConfigurationError
from a13n_ui.model_accounts import (
    DEFAULT_GROK_OAUTH_SCOPE,
    DEFAULT_GROK_OAUTH_SCOPES,
    DEFAULT_GROK_OIDC_SCOPES,
    GrokLoginRequest,
    Provider,
)
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings
from a13n_ui.surfaces import RootOperationStatus, RootOperationView, ThreadMetadataMutation, ThreadMetadataPatch
from a13n_ui.terminal import TuiLaunchOptions
from a13n_ui.terminal import run as run_tui
from a13n_ui.thread_service import RootThreadDefaults


def _parser() -> argparse.ArgumentParser:
    tui_options = argparse.ArgumentParser(add_help=False)
    _add_tui_launch_arguments(tui_options)
    parser = argparse.ArgumentParser(prog="a13n-ui", parents=(tui_options,))
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
    commands.add_parser(
        "tui",
        parents=(tui_options,),
        help="run the interactive terminal workstation (default)",
    )
    commands.add_parser("webui", help="run the bundled WebUI frontend")

    run = commands.add_parser("run", help="execute one headless Thread message")
    run.add_argument("prompt", help="message to execute")
    run.add_argument("--thread", help="continue an existing root Thread")
    run.add_argument("--project", help="Project used when creating a Thread")
    run.add_argument("--agent", help="Agent used when creating a Thread")
    environment_selection = run.add_mutually_exclusive_group()
    environment_selection.add_argument(
        "--environment-mode",
        choices=tuple(item.value for item in EnvironmentMode),
        help="built-in execution mode used when creating a Thread",
    )
    environment_selection.add_argument(
        "--environment-profile",
        help="advanced custom Environment profile used when creating a Thread",
    )
    run.add_argument("--title", help="title used when creating a Thread")
    _add_format(run)

    config = commands.add_parser("config", help="locate, validate, or inspect configuration")
    config_commands = config.add_subparsers(dest="config_command", required=True)
    _add_format(config_commands.add_parser("path", help="show the selected configuration and data paths"))
    _add_format(config_commands.add_parser("validate", help="validate the selected source tree"))
    _add_format(config_commands.add_parser("show", help="show the accepted configuration"))

    import_command = commands.add_parser("import", help="run an explicit external resource conversion")
    import_commands = import_command.add_subparsers(dest="import_command", required=True)
    import_subagents = import_commands.add_parser("subagents", help="preview or apply external subagent imports")
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

    environment = commands.add_parser("environment", help="query Environment modes and profiles")
    environment_commands = environment.add_subparsers(dest="environment_command", required=True)
    environment_list = environment_commands.add_parser("list", help="list Environment modes and profiles")
    _add_format(environment_list)

    thread = commands.add_parser("thread", help="query or archive Threads")
    thread_commands = thread.add_subparsers(dest="thread_command", required=True)
    thread_list = thread_commands.add_parser("list", help="list Threads")
    thread_list.add_argument("--query")
    thread_list.add_argument("--include-archived", action="store_true")
    thread_list.add_argument("--cursor")
    thread_list.add_argument("--limit", type=int, default=20)
    _add_format(thread_list)
    thread_show = thread_commands.add_parser("show", help="inspect one Thread")
    thread_show.add_argument("thread_id")
    thread_show.add_argument("--history-cursor")
    thread_show.add_argument("--history-limit", type=int, default=50)
    _add_format(thread_show)
    thread_archive = thread_commands.add_parser("archive", help="archive one Thread")
    thread_archive.add_argument("thread_id")
    thread_archive.add_argument("--expected-version", type=int, required=True)
    thread_archive.add_argument("--restore", action="store_true")
    _add_format(thread_archive)

    doctor = commands.add_parser("doctor", help="inspect App and extension health")
    _add_format(doctor)

    auth = commands.add_parser("auth", help="inspect or manage compatible Model authentication")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_status = auth_commands.add_parser("status")
    auth_status.add_argument("provider", nargs="?", choices=tuple(item.value for item in Provider))
    _add_format(auth_status)
    auth_login = auth_commands.add_parser("login")
    auth_login.add_argument("provider", choices=tuple(item.value for item in Provider))
    auth_login.add_argument("--allow-account-switch", action="store_true")
    auth_login.add_argument("--device-code", action="store_true")
    _add_format(auth_login)
    auth_logout = auth_commands.add_parser("logout")
    auth_logout.add_argument("provider", choices=tuple(item.value for item in Provider))
    _add_format(auth_logout)
    return parser


def _add_tui_launch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--thread",
        default=argparse.SUPPRESS,
        help="open an existing root Thread in the TUI",
    )
    parser.add_argument(
        "--project",
        default=argparse.SUPPRESS,
        help="override the new-Thread Project for this TUI launch",
    )
    parser.add_argument(
        "--agent",
        default=argparse.SUPPRESS,
        help="override the new-Thread Agent for this TUI launch",
    )
    environment = parser.add_mutually_exclusive_group()
    environment.add_argument(
        "--environment-mode",
        choices=tuple(item.value for item in EnvironmentMode),
        default=argparse.SUPPRESS,
        help="override the built-in Environment mode for this TUI launch",
    )
    environment.add_argument(
        "--environment-profile",
        default=argparse.SUPPRESS,
        help="override the custom Environment profile for this TUI launch",
    )


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
    except KeyboardInterrupt as exc:
        if args.command == "auth" and args.auth_command == "login":
            raise SystemExit(130) from exc
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
    if args.command == "webui":
        from a13n_ui.webui import run as run_web

        await asyncio.to_thread(run_web)
        return 0
    if (
        args.command == "auth"
        and args.auth_command == "login"
        and args.provider == Provider.CODEX.value
        and args.device_code
    ):
        raise ConfigurationError(
            "--device-code is available only for Grok login.",
            code="auth_device_code_unsupported",
        )
    use_grok_device_code = (
        args.command == "auth"
        and args.auth_command == "login"
        and args.provider == Provider.GROK.value
        and args.device_code
    )

    async with open_agent_ui_app(
        settings,
        configuration_path=source.path,
        configuration_error=source.candidate_error,
        codex_login=_codex_cli_login,
        grok_login=lambda request: _grok_cli_login(request, device_code=use_grok_device_code),
    ) as app:
        if args.command == "run":
            configuration = await app.current_configuration()
            if configuration is None:
                raise ConfigurationError(
                    "No accepted Agent UI configuration is available.",
                    code="configuration_unavailable",
                )
            return await _run_one_shot(app, configuration, args)
        if args.command in {"config", "import", "project", "environment", "thread", "doctor", "auth"}:
            return await _run_management(
                app,
                args,
                configuration_path=source.path,
                data_root=settings.storage.data_root,
            )
        await run_tui(app, launch=_tui_launch_options(args))
    return 0


def _tui_launch_options(args: argparse.Namespace) -> TuiLaunchOptions:
    thread_id = getattr(args, "thread", None)
    environment_profile_id = getattr(args, "environment_profile", None)
    environment_mode = getattr(args, "environment_mode", None)
    if environment_mode is not None:
        environment_profile_id = environment_profile_id_for_mode(environment_mode)
    defaults = RootThreadDefaults(
        project_id=getattr(args, "project", None),
        agent_id=getattr(args, "agent", None),
        environment_profile_id=environment_profile_id,
    )
    if thread_id is not None and any(
        value is not None
        for value in (
            defaults.project_id,
            defaults.agent_id,
            defaults.environment_profile_id,
        )
    ):
        raise ConfigurationError(
            "An existing TUI Thread cannot be combined with new-Thread launch overrides.",
            code="tui_arguments_conflict",
        )
    return TuiLaunchOptions(thread_id=thread_id, defaults=defaults)


async def _codex_cli_login(request: object) -> CodexCredentials:
    del request
    flow = CodexOAuthFlow()
    print(f"Open this URL to authenticate Codex:\n{flow.authorization_url()}", file=sys.stderr)
    return await flow.exchange_code_from_callback()


async def _grok_cli_login(request: object, *, device_code: bool) -> GrokCredentials:
    if not isinstance(request, GrokLoginRequest):
        raise TypeError("Grok login requires GrokLoginRequest")
    try:
        issuer, client_id = request.scope.rsplit("::", 1)
    except ValueError as exc:
        raise ConfigurationError(
            "The selected Grok authentication scope is invalid.",
            code="account_scope_incompatible",
        ) from exc
    if not issuer or not client_id:
        raise ConfigurationError(
            "The selected Grok authentication scope is invalid.",
            code="account_scope_incompatible",
        )
    scopes = DEFAULT_GROK_OAUTH_SCOPES if request.scope == DEFAULT_GROK_OAUTH_SCOPE else DEFAULT_GROK_OIDC_SCOPES
    if device_code:
        authorization = await GrokDeviceAuthorizationFlow.start(
            issuer=issuer,
            client_id=client_id,
            scopes=scopes,
            referrer="agent-ui",
        )
        url = authorization.verification_uri_complete or authorization.verification_uri
        print(f"Open this URL to authenticate Grok:\n{url}", file=sys.stderr)
        print(f"Confirm this code in your browser: {authorization.user_code}", file=sys.stderr)
        print("Waiting for Grok authorization...", file=sys.stderr)
        return await authorization.wait_for_credentials()

    flow = await GrokOAuthFlow.discover(
        issuer=issuer,
        client_id=client_id,
        scopes=scopes,
        referrer="agent-ui",
    )
    print(f"Open this URL to authenticate Grok:\n{flow.authorization_url()}", file=sys.stderr)
    return await flow.exchange_code_from_callback(timeout_seconds=600)


async def _run_management(
    app: AgentUiApp,
    args: argparse.Namespace,
    *,
    configuration_path: Path | None = None,
    data_root: Path | None = None,
) -> int:
    if args.command == "config":
        if args.config_command == "path":
            _print_projection(
                {
                    "configuration_path": None if configuration_path is None else str(configuration_path),
                    "data_root": None if data_root is None else str(data_root),
                },
                args.format,
            )
            return 0
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
    if args.command == "import":
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

    if args.command == "environment":
        _print_projection({"environment_profiles": await app.environment_profiles()}, args.format)
        return 0

    if args.command == "thread":
        if args.thread_command == "list":
            page = await app.list_threads(
                query=args.query,
                include_archived=args.include_archived,
                cursor=args.cursor,
                limit=args.limit,
            )
            _print_projection(page, args.format)
            return 0
        if args.thread_command == "show":
            thread = await app.get_thread(args.thread_id)
            history = await app.get_thread_transcript(
                thread_id=args.thread_id,
                cursor=args.history_cursor,
                limit=args.history_limit,
            )
            _print_projection({"thread": thread, "transcript": history}, args.format)
            return 0
        thread = await app.update_thread_metadata(
            thread_id=args.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=args.expected_version,
                patch=ThreadMetadataPatch(archived=not args.restore),
            ),
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

    if args.auth_command == "status" and args.provider is None:
        result: object = {
            "accounts": [
                await app.inspect_model_account(Provider.CODEX),
                await app.inspect_model_account(Provider.GROK),
            ]
        }
    else:
        provider = Provider(args.provider)
        if args.auth_command == "status":
            result = await app.inspect_model_account(provider)
        elif args.auth_command == "login":
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
                args.environment_mode,
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

    receipt = await app.submit_thread(thread_id=thread_id, prompt=args.prompt)
    operation = await app.wait_root_operation(receipt.receipt_id)
    if args.format == "json":
        print(operation.model_dump_json())
    else:
        _print_text_result(operation)
    return 0 if operation.status is RootOperationStatus.completed else 1


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
    environment_profile_id = args.environment_profile
    if args.environment_mode is not None:
        environment_profile_id = environment_profile_id_for_mode(args.environment_mode)
    return RootThreadDefaults(
        project_id=project_id,
        agent_id=agent_id,
        environment_profile_id=(environment_profile_id or defaults.environment_profile),
    )


def _print_text_result(operation: RootOperationView) -> None:
    outcome = operation.outcome
    if operation.status is RootOperationStatus.completed and outcome is not None:
        output = outcome.execution.output
        print(output if isinstance(output, str) else json.dumps(output, ensure_ascii=False))
        cleanup_count = len(outcome.environment.cleanup_failures)
        if cleanup_count:
            print(f"Environment cleanup reported {cleanup_count} error(s).", file=sys.stderr)
        return
    failure = operation.failure or (None if outcome is None else outcome.execution.failure)
    if failure is not None:
        print(f"{failure.code}: {failure.message}", file=sys.stderr)
    elif operation.status is RootOperationStatus.suspended:
        print("Run suspended with deferred tool requests.", file=sys.stderr)
    else:
        print(f"Run {operation.status.value}.", file=sys.stderr)


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
