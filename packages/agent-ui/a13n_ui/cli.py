"""Executable bootstrap for the Agent UI interactive and one-shot CLI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any

import click
from a13n_harness.model_auth import (
    CodexCredentials,
    GrokCredentials,
)
from a13n_logging import LogFormat, configure_logging
from anyio import fail_after
from pydantic import BaseModel, SecretStr, ValidationError

from a13n_ui.app import AgentUiApp, open_agent_ui_app
from a13n_ui.configuration import (
    ExternalSubagentProduct,
    ExternalSubagentScope,
    LoadedAgentUiConfiguration,
)
from a13n_ui.content_plugins import ContentPluginStore, InstalledContentPlugin
from a13n_ui.environment_profiles import EnvironmentMode, environment_profile_id_for_mode
from a13n_ui.errors import AgentUiError, ConfigurationError
from a13n_ui.model_accounts import (
    GrokLoginRequest,
    Provider,
)
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings
from a13n_ui.surfaces import (
    NewThreadDefaults,
    RootOperationStatus,
    RootOperationView,
    ThreadMetadataMutation,
    ThreadMetadataPatch,
)
from a13n_ui.terminal import TuiLaunchOptions
from a13n_ui.terminal import run as run_tui
from a13n_ui.thread_service import RootThreadDefaults


class OutputFormat(StrEnum):
    """Supported stable terminal output formats."""

    text = "text"
    json = "json"


@dataclass(frozen=True, slots=True)
class CliRequest:
    """Typed command request passed from Click into the async application boundary."""

    command: str | None = None
    action: str | None = None
    config_path: Path | None = None
    data_root: Path | None = None
    output_format: OutputFormat = OutputFormat.text
    prompt: str | None = None
    thread_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    environment_mode: str | None = None
    environment_profile_id: str | None = None
    title: str | None = None
    product: str | None = None
    scope: str | None = None
    project_root: Path | None = None
    user_home: Path | None = None
    apply: bool = False
    repository: str | None = None
    plugin_id: str | None = None
    ref: str | None = None
    query: str | None = None
    include_archived: bool = False
    cursor: str | None = None
    limit: int = 20
    history_cursor: str | None = None
    history_limit: int = 50
    expected_version: int | None = None
    restore: bool = False
    provider: str | None = None
    allow_account_switch: bool = False
    device_code: bool = False
    credential_key: SecretStr | None = field(default=None, repr=False)
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_api_key: str | None = None
    dangerously_bypass_permission: bool = False


@dataclass(frozen=True, slots=True)
class _CliContext:
    config_path: Path | None
    data_root: Path | None
    thread_id: str | None
    project_id: str | None
    agent_id: str | None
    environment_mode: str | None
    environment_profile_id: str | None


_CONTEXT_SETTINGS = {"help_option_names": ("-h", "--help")}
_FORMAT_CHOICE = click.Choice(tuple(item.value for item in OutputFormat), case_sensitive=True)
_ENVIRONMENT_MODE_CHOICE = click.Choice(tuple(item.value for item in EnvironmentMode), case_sensitive=True)
_PROVIDER_CHOICE = click.Choice(tuple(item.value for item in Provider), case_sensitive=True)
_PRODUCT_CHOICE = click.Choice(tuple(item.value for item in ExternalSubagentProduct), case_sensitive=True)
_SCOPE_CHOICE = click.Choice(tuple(item.value for item in ExternalSubagentScope), case_sensitive=True)
_PATH = click.Path(path_type=Path)


@click.group(
    name="a13n-ui",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings=_CONTEXT_SETTINGS,
)
@click.option(
    "--config",
    "config_path",
    type=_PATH,
    help="Explicit Agent UI configuration YAML (default: ~/.a13n-ui/a13n-ui.yaml).",
)
@click.option("--data-root", type=_PATH, help="Override the local Agent UI data root.")
@click.option("--thread", "thread_id", help="Open an existing root Thread in the TUI.")
@click.option("--project", "project_id", help="Override the new-Thread Project for this TUI launch.")
@click.option("--agent", "agent_id", help="Override the new-Thread Agent for this TUI launch.")
@click.option(
    "--environment-mode",
    type=_ENVIRONMENT_MODE_CHOICE,
    help="Override the built-in Environment mode for this TUI launch.",
)
@click.option(
    "--environment-profile",
    "environment_profile_id",
    help="Override the custom Environment profile for this TUI launch.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: Path | None,
    data_root: Path | None,
    thread_id: str | None,
    project_id: str | None,
    agent_id: str | None,
    environment_mode: str | None,
    environment_profile_id: str | None,
) -> None:
    """Run the Agent UI workstation and management commands."""

    ctx.obj = _CliContext(
        config_path=config_path,
        data_root=data_root,
        thread_id=thread_id,
        project_id=project_id,
        agent_id=agent_id,
        environment_mode=environment_mode,
        environment_profile_id=environment_profile_id,
    )
    _validate_environment_selection(environment_mode, environment_profile_id)
    if ctx.invoked_subcommand is not None:
        return
    _execute(
        CliRequest(
            config_path=config_path,
            data_root=data_root,
            thread_id=thread_id,
            project_id=project_id,
            agent_id=agent_id,
            environment_mode=environment_mode,
            environment_profile_id=environment_profile_id,
        )
    )


@cli.command("tui")
@click.option("--thread", "thread_id", help="Open an existing root Thread.")
@click.option("--project", "project_id", help="Override the new-Thread Project.")
@click.option("--agent", "agent_id", help="Override the new-Thread Agent.")
@click.option(
    "--environment-mode",
    type=_ENVIRONMENT_MODE_CHOICE,
    help="Override the built-in Environment mode.",
)
@click.option(
    "--environment-profile",
    "environment_profile_id",
    help="Override the custom Environment profile.",
)
@click.pass_context
def tui_command(
    ctx: click.Context,
    thread_id: str | None,
    project_id: str | None,
    agent_id: str | None,
    environment_mode: str | None,
    environment_profile_id: str | None,
) -> None:
    """Run the interactive terminal workstation."""

    root = _root_context(ctx)
    thread_id = thread_id if thread_id is not None else root.thread_id
    project_id = project_id if project_id is not None else root.project_id
    agent_id = agent_id if agent_id is not None else root.agent_id
    environment_mode = environment_mode if environment_mode is not None else root.environment_mode
    environment_profile_id = (
        environment_profile_id if environment_profile_id is not None else root.environment_profile_id
    )
    _validate_environment_selection(environment_mode, environment_profile_id)
    _execute(
        _request(
            ctx,
            command="tui",
            thread_id=thread_id,
            project_id=project_id,
            agent_id=agent_id,
            environment_mode=environment_mode,
            environment_profile_id=environment_profile_id,
        )
    )


@cli.command("setup")
@click.pass_context
def setup_command(ctx: click.Context) -> None:
    """Open interactive setup, or inspect setup status with --format json."""
    _execute(_request(ctx, command="setup"))


@cli.command("webui")
@click.option("--host", default="127.0.0.1", show_default=True, help="Listener IPv4 or IPv6 address.")
@click.option("--port", type=click.IntRange(1, 65535), default=8765, show_default=True)
@click.option("--api-key", default=None, help="Explicit process-local API key (visible in shell arguments).")
@click.option("--dangerously-bypass-permission", is_flag=True, help="Disable API authentication for this listener.")
@click.pass_context
def webui_command(
    ctx: click.Context, host: str, port: int, api_key: str | None, dangerously_bypass_permission: bool
) -> None:
    """Run one foreground WebUI server and its in-memory App."""

    _execute(
        _request(
            ctx,
            command="webui",
            web_host=host,
            web_port=port,
            web_api_key=api_key,
            dangerously_bypass_permission=dangerously_bypass_permission,
        )
    )


@cli.command("run")
@click.argument("prompt")
@click.option("--thread", "thread_id", help="Continue an existing root Thread.")
@click.option("--project", "project_id", help="Project used when creating a Thread.")
@click.option("--agent", "agent_id", help="Agent used when creating a Thread.")
@click.option(
    "--environment-mode",
    type=_ENVIRONMENT_MODE_CHOICE,
    help="Built-in execution mode used when creating a Thread.",
)
@click.option(
    "--environment-profile",
    "environment_profile_id",
    help="Custom Environment profile used when creating a Thread.",
)
@click.option("--title", help="Title used when creating a Thread.")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def run_command(
    ctx: click.Context,
    prompt: str,
    thread_id: str | None,
    project_id: str | None,
    agent_id: str | None,
    environment_mode: str | None,
    environment_profile_id: str | None,
    title: str | None,
    output_format: str,
) -> None:
    """Execute one headless Thread message."""

    _validate_environment_selection(environment_mode, environment_profile_id)
    _execute(
        _request(
            ctx,
            command="run",
            prompt=prompt,
            thread_id=thread_id,
            project_id=project_id,
            agent_id=agent_id,
            environment_mode=environment_mode,
            environment_profile_id=environment_profile_id,
            title=title,
            output_format=OutputFormat(output_format),
        )
    )


@cli.group("config")
def config_group() -> None:
    """Locate, validate, or inspect configuration."""


@config_group.command("path")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def config_path_command(ctx: click.Context, output_format: str) -> None:
    """Show the selected configuration and data paths."""

    _execute(_request(ctx, command="config", action="path", output_format=OutputFormat(output_format)))


@config_group.command("validate")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def config_validate_command(ctx: click.Context, output_format: str) -> None:
    """Validate the selected source tree."""

    _execute(_request(ctx, command="config", action="validate", output_format=OutputFormat(output_format)))


@config_group.command("show")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def config_show_command(ctx: click.Context, output_format: str) -> None:
    """Show the accepted configuration."""

    _execute(_request(ctx, command="config", action="show", output_format=OutputFormat(output_format)))


@cli.group("import")
def import_group() -> None:
    """Run an explicit external resource conversion."""


@import_group.command("subagents")
@click.option("--product", type=_PRODUCT_CHOICE, required=True)
@click.option("--scope", type=_SCOPE_CHOICE, required=True)
@click.option("--project-root", type=_PATH)
@click.option("--user-home", type=_PATH)
@click.option("--apply", is_flag=True, help="Apply every ready candidate; omission is a dry-run preview.")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def import_subagents_command(
    ctx: click.Context,
    product: str,
    scope: str,
    project_root: Path | None,
    user_home: Path | None,
    apply: bool,
    output_format: str,
) -> None:
    """Preview or apply external subagent imports."""

    _execute(
        _request(
            ctx,
            command="import",
            action="subagents",
            product=product,
            scope=scope,
            project_root=project_root,
            user_home=user_home,
            apply=apply,
            output_format=OutputFormat(output_format),
        )
    )


@cli.group("plugin")
def plugin_group() -> None:
    """Install or manage declarative Content Plugins."""


@plugin_group.command("install")
@click.argument("repository")
@click.option("--plugin", "plugin_id", help="Plugin ID when the repository contains several.")
@click.option("--ref", help="Git branch, tag, or commit to install.")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def plugin_install_command(
    ctx: click.Context,
    repository: str,
    plugin_id: str | None,
    ref: str | None,
    output_format: str,
) -> None:
    """Install one Content Plugin from a Git repository."""

    _execute(
        _request(
            ctx,
            command="plugin",
            action="install",
            repository=repository,
            plugin_id=plugin_id,
            ref=ref,
            output_format=OutputFormat(output_format),
        )
    )


@plugin_group.command("list")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def plugin_list_command(ctx: click.Context, output_format: str) -> None:
    """List installed Content Plugins and their directories."""

    _execute(_request(ctx, command="plugin", action="list", output_format=OutputFormat(output_format)))


@plugin_group.command("uninstall")
@click.argument("plugin_id")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def plugin_uninstall_command(ctx: click.Context, plugin_id: str, output_format: str) -> None:
    """Unregister one Content Plugin."""

    _execute(
        _request(
            ctx,
            command="plugin",
            action="uninstall",
            plugin_id=plugin_id,
            output_format=OutputFormat(output_format),
        )
    )


@cli.group("project")
def project_group() -> None:
    """Query configured Projects."""


@project_group.command("list")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def project_list_command(ctx: click.Context, output_format: str) -> None:
    """List Projects."""

    _execute(_request(ctx, command="project", action="list", output_format=OutputFormat(output_format)))


@cli.group("environment")
def environment_group() -> None:
    """Query Environment modes and profiles."""


@environment_group.command("list")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def environment_list_command(ctx: click.Context, output_format: str) -> None:
    """List Environment modes and profiles."""

    _execute(_request(ctx, command="environment", action="list", output_format=OutputFormat(output_format)))


@cli.group("thread")
def thread_group() -> None:
    """Query or archive Threads."""


@thread_group.command("list")
@click.option("--query")
@click.option("--include-archived", is_flag=True)
@click.option("--cursor")
@click.option("--limit", type=click.IntRange(min=1), default=20, show_default=True)
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def thread_list_command(
    ctx: click.Context,
    query: str | None,
    include_archived: bool,
    cursor: str | None,
    limit: int,
    output_format: str,
) -> None:
    """List Threads."""

    _execute(
        _request(
            ctx,
            command="thread",
            action="list",
            query=query,
            include_archived=include_archived,
            cursor=cursor,
            limit=limit,
            output_format=OutputFormat(output_format),
        )
    )


@thread_group.command("show")
@click.argument("thread_id")
@click.option("--history-cursor")
@click.option("--history-limit", type=click.IntRange(min=1), default=50, show_default=True)
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def thread_show_command(
    ctx: click.Context,
    thread_id: str,
    history_cursor: str | None,
    history_limit: int,
    output_format: str,
) -> None:
    """Inspect one Thread and its transcript."""

    _execute(
        _request(
            ctx,
            command="thread",
            action="show",
            thread_id=thread_id,
            history_cursor=history_cursor,
            history_limit=history_limit,
            output_format=OutputFormat(output_format),
        )
    )


@thread_group.command("archive")
@click.argument("thread_id")
@click.option("--expected-version", type=int, required=True)
@click.option("--restore", is_flag=True, help="Restore the Thread instead of archiving it.")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def thread_archive_command(
    ctx: click.Context,
    thread_id: str,
    expected_version: int,
    restore: bool,
    output_format: str,
) -> None:
    """Archive or restore one Thread."""

    _execute(
        _request(
            ctx,
            command="thread",
            action="archive",
            thread_id=thread_id,
            expected_version=expected_version,
            restore=restore,
            output_format=OutputFormat(output_format),
        )
    )


@cli.command("doctor")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def doctor_command(ctx: click.Context, output_format: str) -> None:
    """Inspect App and extension health."""

    _execute(_request(ctx, command="doctor", output_format=OutputFormat(output_format)))


@cli.group("auth")
def auth_group() -> None:
    """Inspect or manage compatible Model authentication."""


@auth_group.command("status")
@click.argument("provider", required=False, type=_PROVIDER_CHOICE)
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def auth_status_command(ctx: click.Context, provider: str | None, output_format: str) -> None:
    """Show Model authentication status."""

    _execute(
        _request(
            ctx,
            command="auth",
            action="status",
            provider=provider,
            output_format=OutputFormat(output_format),
        )
    )


@auth_group.group("key")
def auth_key_group() -> None:
    """Manage Host-local plaintext API keys without returning key bytes."""


@auth_key_group.command("list")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text")
@click.pass_context
def auth_key_list(ctx: click.Context, output_format: str) -> None:
    _execute(_request(ctx, command="auth", action="key-list", output_format=OutputFormat(output_format)))


@auth_key_group.command("set")
@click.argument("reference")
@click.pass_context
def auth_key_set(ctx: click.Context, reference: str) -> None:
    """Add or replace a key using a hidden prompt, never a command-line key."""
    from a13n_ui.model_accounts.api_keys import ApiKeyInput

    secret = SecretStr(click.prompt("API key", hide_input=True, err=True))
    try:
        value = ApiKeyInput(credential_ref=reference, key=secret)
    except ValidationError:
        raise click.BadParameter("Use a valid reference such as key-primary and a nonempty key.") from None
    _execute(_request(ctx, command="auth", action="key-set", ref=value.credential_ref, credential_key=value.key))


@auth_key_group.command("delete")
@click.argument("reference")
@click.confirmation_option(prompt="Delete this key? Future model resolution using it will fail.")
@click.pass_context
def auth_key_delete(ctx: click.Context, reference: str) -> None:
    _execute(_request(ctx, command="auth", action="key-delete", ref=reference))


@auth_group.command("login")
@click.argument("provider", type=_PROVIDER_CHOICE)
@click.option("--allow-account-switch", is_flag=True)
@click.option(
    "--device-code/--browser", default=True, help="Device authorization (default) or a local browser callback."
)
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def auth_login_command(
    ctx: click.Context,
    provider: str,
    allow_account_switch: bool,
    device_code: bool,
    output_format: str,
) -> None:
    """Authenticate a compatible Model provider."""

    _execute(
        _request(
            ctx,
            command="auth",
            action="login",
            provider=provider,
            allow_account_switch=allow_account_switch,
            device_code=device_code,
            output_format=OutputFormat(output_format),
        )
    )


@auth_group.command("logout")
@click.argument("provider", type=_PROVIDER_CHOICE)
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def auth_logout_command(ctx: click.Context, provider: str, output_format: str) -> None:
    """Remove locally stored Model credentials."""

    _execute(
        _request(
            ctx,
            command="auth",
            action="logout",
            provider=provider,
            output_format=OutputFormat(output_format),
        )
    )


def _root_context(ctx: click.Context) -> _CliContext:
    root = ctx.find_root().obj
    if not isinstance(root, _CliContext):
        raise RuntimeError("Agent UI CLI context is unavailable")
    return root


def _request(ctx: click.Context, **values: Any) -> CliRequest:
    root = _root_context(ctx)
    return CliRequest(config_path=root.config_path, data_root=root.data_root, **values)


def _validate_environment_selection(mode: str | None, profile_id: str | None) -> None:
    if mode is not None and profile_id is not None:
        raise click.UsageError("--environment-mode cannot be combined with --environment-profile.")


def _execute(request: CliRequest) -> None:
    try:
        exit_code = asyncio.run(_run(request))
    except KeyboardInterrupt as exc:
        if request.command == "auth" and request.action == "login":
            raise click.exceptions.Exit(130) from exc
        return
    except AgentUiError as exc:
        if request.output_format is OutputFormat.json:
            click.echo(json.dumps({"error": {"code": exc.code, "message": str(exc)}}, ensure_ascii=False))
        else:
            click.echo(f"Error [{exc.code}]: {exc}", err=True)
        raise click.exceptions.Exit(1) from exc
    if exit_code:
        raise click.exceptions.Exit(exit_code)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the Click command tree from the console-script boundary."""

    try:
        exit_code = cli.main(args=None if argv is None else list(argv), prog_name="a13n-ui", standalone_mode=False)
        if isinstance(exit_code, int) and exit_code:
            raise SystemExit(exit_code)
    except click.exceptions.Exit as exc:
        if exc.exit_code:
            raise SystemExit(exc.exit_code) from exc
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except click.Abort as exc:
        click.echo("Aborted!", err=True)
        raise SystemExit(1) from exc


async def _run(request: CliRequest) -> int:
    source = await load_agent_ui_settings(request.config_path, data_root=request.data_root)
    await asyncio.to_thread(ensure_default_directories, source)
    settings = source.settings
    configure_logging(
        level=settings.log_level,
        log_format=LogFormat(settings.log_format),
        logger_names=("a13n_ui",),
    )
    if request.command == "plugin":
        return await _run_content_plugins(request, settings.storage.data_root)
    if request.command == "webui":
        from a13n_ui.webui import run as run_web

        await run_web(
            lambda: open_agent_ui_app(
                settings, configuration_path=source.path, configuration_error=source.candidate_error, host_mode="webui"
            ),
            host=request.web_host,
            port=request.web_port,
            api_key=request.web_api_key,
            dangerously_bypass_permission=request.dangerously_bypass_permission,
        )
        return 0
    use_device_code = request.device_code

    def app_factory() -> AbstractAsyncContextManager[AgentUiApp]:
        return open_agent_ui_app(
            settings,
            configuration_path=source.path,
            configuration_error=source.candidate_error,
            codex_login=lambda request: _codex_cli_login(request, device_code=use_device_code),
            grok_login=lambda request: _grok_cli_login(request, device_code=use_device_code),
        )

    if request.command in {None, "tui"} or (request.command == "setup" and request.output_format is OutputFormat.text):
        await run_tui(app_factory, launch=_tui_launch_options(request))
        return 0

    async with app_factory() as app:
        if request.command == "setup":
            click.echo((await app.setup_status(rediscover=True)).model_dump_json(indent=2))
            return 0
        if request.command == "run":
            configuration = await app.current_configuration()
            if configuration is None:
                raise ConfigurationError(
                    "No accepted Agent UI configuration is available.",
                    code="configuration_unavailable",
                )
            return await _run_one_shot(app, configuration, request)
        return await _run_management(
            app,
            request,
            configuration_path=source.path,
            data_root=settings.storage.data_root,
        )


async def _run_content_plugins(request: CliRequest, data_root: Path) -> int:
    store = ContentPluginStore(data_root / "content-plugins")
    if request.action == "install":
        if request.repository is None:
            raise RuntimeError("Plugin install requires a repository")
        installed = await store.install(
            request.repository,
            plugin_id=request.plugin_id,
            ref=request.ref,
        )
        _print_projection({"installed": _content_plugin_projection(installed)}, request.output_format)
        return 0
    if request.action == "list":
        plugins = await store.list()
        _print_projection(
            {
                "plugins": tuple(_content_plugin_projection(item) for item in plugins),
                "diagnostics": tuple(store.diagnostics),
            },
            request.output_format,
        )
        return 0
    if request.plugin_id is None:
        raise RuntimeError("Plugin uninstall requires a plugin ID")
    removed = await store.uninstall(request.plugin_id)
    _print_projection(removed, request.output_format)
    return 0


def _content_plugin_projection(plugin: InstalledContentPlugin) -> dict[str, str]:
    return {
        "plugin_id": plugin.plugin_id,
        "name": plugin.name,
        "version": plugin.version,
        "description": plugin.description,
        "repository": plugin.repository,
        "commit": plugin.commit,
        "path": plugin.path,
    }


def _tui_launch_options(request: CliRequest) -> TuiLaunchOptions:
    environment_profile_id = request.environment_profile_id
    if request.environment_mode is not None:
        environment_profile_id = environment_profile_id_for_mode(request.environment_mode)
    defaults = NewThreadDefaults(
        project_id=request.project_id,
        agent_id=request.agent_id,
        environment_profile_id=environment_profile_id,
    )
    if request.thread_id is not None and any(
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
    return TuiLaunchOptions(thread_id=request.thread_id, defaults=defaults, show_setup=request.command == "setup")


def _present_login(**values: object) -> None:
    if values.get("verification_url"):
        click.echo(f"Open this URL to authenticate:\n{values['verification_url']}", err=True)
    if values.get("user_code"):
        click.echo(f"Confirm this code in your browser: {values['user_code']}", err=True)
    if values.get("message"):
        click.echo(values["message"], err=True)
    click.echo("Waiting for authorization (Ctrl+C to cancel)...", err=True)


async def _codex_cli_login(request: object, *, device_code: bool = True) -> CodexCredentials:
    from a13n_ui.model_accounts.codex import CodexLoginRequest
    from a13n_ui.model_accounts.login import authorize_codex

    if not isinstance(request, CodexLoginRequest):
        raise TypeError("Codex login requires CodexLoginRequest")
    with fail_after(900):
        return await authorize_codex(request, "device" if device_code else "browser", _present_login)


async def _grok_cli_login(request: object, *, device_code: bool = True) -> GrokCredentials:
    from a13n_ui.model_accounts.login import authorize_grok

    if not isinstance(request, GrokLoginRequest):
        raise TypeError("Grok login requires GrokLoginRequest")
    with fail_after(900):
        return await authorize_grok(request, "device" if device_code else "browser", _present_login)


async def _run_management(
    app: AgentUiApp,
    request: CliRequest,
    *,
    configuration_path: Path | None = None,
    data_root: Path | None = None,
) -> int:
    if request.command == "config":
        if request.action == "path":
            _print_projection(
                {
                    "configuration_path": None if configuration_path is None else str(configuration_path),
                    "data_root": None if data_root is None else str(data_root),
                },
                request.output_format,
            )
            return 0
        if request.action == "validate":
            status = await app.status()
            projection = {
                "valid": status.candidate_error_code is None,
                "content_plugin_diagnostics": status.content_plugin_diagnostics,
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
            _print_projection(projection, request.output_format)
            return 0 if projection["valid"] else 1
        if request.action == "show":
            configuration = await _require_configuration(app)
            _print_projection(configuration.model_dump(mode="json"), request.output_format)
            return 0
    if request.command == "import":
        if request.product is None or request.scope is None:
            raise RuntimeError("Subagent import requires a product and scope")
        preview = await app.preview_subagent_import(
            product=request.product,
            scope=request.scope,
            project_root=request.project_root,
            user_home=request.user_home,
        )
        applied: list[object] = []
        if request.apply:
            for candidate in preview.candidates:
                if candidate.status == "ready":
                    applied.append(await app.apply_subagent_import(candidate))
        _print_projection(
            {
                "dry_run": not request.apply,
                "preview": preview,
                "applied": applied,
            },
            request.output_format,
        )
        return 0 if all(item.status != "invalid" for item in preview.candidates) else 1

    if request.command == "project":
        _print_projection({"projects": await app.projects()}, request.output_format)
        return 0

    if request.command == "environment":
        _print_projection({"environment_profiles": await app.environment_profiles()}, request.output_format)
        return 0

    if request.command == "thread":
        if request.action == "list":
            page = await app.list_threads(
                query=request.query,
                include_archived=request.include_archived,
                cursor=request.cursor,
                limit=request.limit,
            )
            _print_projection(page, request.output_format)
            return 0
        if request.action == "show":
            if request.thread_id is None:
                raise RuntimeError("Thread show requires a Thread ID")
            thread = await app.get_thread(request.thread_id)
            history = await app.get_thread_transcript(
                thread_id=request.thread_id,
                cursor=request.history_cursor,
                limit=request.history_limit,
            )
            _print_projection({"thread": thread, "transcript": history}, request.output_format)
            return 0
        if request.thread_id is None or request.expected_version is None:
            raise RuntimeError("Thread archive requires a Thread ID and expected version")
        thread = await app.update_thread_metadata(
            thread_id=request.thread_id,
            mutation=ThreadMetadataMutation(
                expected_version=request.expected_version,
                patch=ThreadMetadataPatch(archived=not request.restore),
            ),
        )
        _print_projection(thread, request.output_format)
        return 0

    if request.command == "doctor":
        status = await app.status()
        references = await app.list_catalog()
        projection = {
            "status": status,
            "catalog": {
                "total": len(references),
                "ambiguous": [item for item in references if not item.configurable],
            },
        }
        _print_projection(projection, request.output_format)
        return 0 if status.candidate_error_code is None else 1

    if request.command != "auth" or request.action is None:
        raise RuntimeError("Unsupported Agent UI CLI command")
    if request.action.startswith("key-"):
        from a13n_ui.model_accounts.api_keys import ApiKeyInput

        if request.action == "key-list":
            _print_projection(await app.list_api_keys(), request.output_format)
        elif request.action == "key-set" and request.ref is not None and request.credential_key is not None:
            _print_projection(
                await app.put_api_key(ApiKeyInput(credential_ref=request.ref, key=request.credential_key)),
                request.output_format,
            )
        elif request.action == "key-delete" and request.ref is not None:
            await app.delete_api_key(request.ref)
            _print_projection({"credential_ref": request.ref, "deleted": True}, request.output_format)
        return 0
    if request.action == "status" and request.provider is None:
        result: object = {
            "accounts": [
                await app.inspect_model_account(Provider.CODEX),
                await app.inspect_model_account(Provider.GROK),
            ]
        }
    else:
        if request.provider is None:
            raise RuntimeError("Authentication command requires a provider")
        provider = Provider(request.provider)
        if request.action == "status":
            result = await app.inspect_model_account(provider)
        elif request.action == "login":
            result = await app.login_model_account(
                provider,
                allow_account_switch=request.allow_account_switch,
            )
        else:
            result = {"provider": provider.value, "logged_out": await app.logout_model_account(provider)}
    _print_projection(result, request.output_format)
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
    request: CliRequest,
) -> int:
    if request.prompt is None:
        raise RuntimeError("Headless Run requires a prompt")
    thread_id = request.thread_id
    if thread_id is not None:
        if any(
            value is not None
            for value in (
                request.project_id,
                request.agent_id,
                request.environment_mode,
                request.environment_profile_id,
                request.title,
            )
        ):
            raise ConfigurationError(
                "Thread creation options cannot be used with --thread.",
                code="run_arguments_conflict",
            )
    else:
        defaults = _new_thread_defaults(configuration, request)
        thread = await app.create_thread(defaults=defaults, title=request.title)
        thread_id = thread.thread_id

    receipt = await app.submit_thread(thread_id=thread_id, prompt=request.prompt)
    operation = await app.wait_root_operation(receipt.receipt_id)
    if request.output_format is OutputFormat.json:
        click.echo(operation.model_dump_json())
    else:
        _print_text_result(operation)
    return 0 if operation.status is RootOperationStatus.completed else 1


def _new_thread_defaults(
    configuration: LoadedAgentUiConfiguration,
    request: CliRequest,
) -> RootThreadDefaults:
    defaults = configuration.document.defaults
    project_id = request.project_id or defaults.project
    agent_id = request.agent_id or defaults.agent
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
    environment_profile_id = request.environment_profile_id
    if request.environment_mode is not None:
        environment_profile_id = environment_profile_id_for_mode(request.environment_mode)
    return RootThreadDefaults(
        project_id=project_id,
        agent_id=agent_id,
        environment_profile_id=(environment_profile_id or defaults.environment_profile),
    )


def _print_text_result(operation: RootOperationView) -> None:
    outcome = operation.outcome
    if operation.status is RootOperationStatus.completed and outcome is not None:
        output = outcome.execution.output
        if isinstance(output, str):
            click.echo(output)
        else:
            click.echo(_render_text(_jsonable(output)))
        cleanup_count = len(outcome.environment.cleanup_failures)
        if cleanup_count:
            noun = "failure" if cleanup_count == 1 else "failures"
            click.echo(f"Warning: environment cleanup reported {cleanup_count} {noun}.", err=True)
        return
    failure = operation.failure or (None if outcome is None else outcome.execution.failure)
    if failure is not None:
        click.echo(f"Error [{failure.code}]: {failure.message}", err=True)
        if failure.retry_hint:
            click.echo(f"Retry: {failure.retry_hint}", err=True)
    elif operation.status is RootOperationStatus.suspended:
        click.echo("Run suspended: deferred tool requests require attention.", err=True)
    else:
        click.echo(f"Run ended with status: {operation.status.value}.", err=True)


def _print_projection(value: object, output_format: OutputFormat) -> None:
    payload = _jsonable(value)
    if output_format is OutputFormat.json:
        click.echo(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    else:
        click.echo(_render_text(payload))


def _render_text(value: object) -> str:
    lines = _value_lines(value, indent=0)
    return "\n".join(lines) if lines else "-"


def _value_lines(value: object, *, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        if not value:
            return [f"{prefix}-"]
        lines: list[str] = []
        for key, item in value.items():
            lines.extend(_field_lines(str(key), item, indent=indent))
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}-"]
        lines = []
        for item in value:
            lines.extend(_list_item_lines(item, indent=indent))
        return lines
    return [f"{prefix}{_text_scalar(value)}"]


def _field_lines(key: str, value: object, *, indent: int) -> list[str]:
    prefix = " " * indent
    label = _humanize_label(key)
    if isinstance(value, Mapping):
        if not value:
            return [f"{prefix}{label}: -"]
        return [f"{prefix}{label}:", *_value_lines(value, indent=indent + 2)]
    if isinstance(value, list):
        if not value:
            return [f"{prefix}{label} (0): -"]
        return [f"{prefix}{label} ({len(value)}):", *_value_lines(value, indent=indent + 2)]
    return [f"{prefix}{label}: {_text_scalar(value)}"]


def _list_item_lines(value: object, *, indent: int) -> list[str]:
    prefix = " " * indent
    if isinstance(value, Mapping):
        if not value:
            return [f"{prefix}-"]
        fields_iter = iter(value.items())
        first_key, first_value = next(fields_iter)
        first_lines = _field_lines(str(first_key), first_value, indent=indent + 2)
        lines = [f"{prefix}- {first_lines[0][indent + 2 :]}", *first_lines[1:]]
        for key, item in fields_iter:
            lines.extend(_field_lines(str(key), item, indent=indent + 2))
        return lines
    if isinstance(value, list):
        if not value:
            return [f"{prefix}- -"]
        return [f"{prefix}-", *_value_lines(value, indent=indent + 2)]
    return [f"{prefix}- {_text_scalar(value)}"]


def _humanize_label(value: str) -> str:
    replacements = {"id": "ID", "ids": "IDs", "url": "URL", "uri": "URI"}
    words = value.replace("-", "_").split("_")
    rendered = [replacements.get(word.lower(), word.lower()) for word in words]
    if rendered:
        rendered[0] = rendered[0] if rendered[0] in replacements.values() else rendered[0].capitalize()
    return " ".join(rendered)


def _text_scalar(value: object) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


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


__all__ = ["CliRequest", "OutputFormat", "cli", "main"]
