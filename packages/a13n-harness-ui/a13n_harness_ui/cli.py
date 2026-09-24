"""Executable bootstrap for the Harness UI interactive and one-shot CLI."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from pydantic import SecretStr


class OutputFormat(StrEnum):
    """Supported stable terminal output formats."""

    text = "text"
    json = "json"


@dataclass(frozen=True, slots=True)
class CliRequest:
    """Typed command request passed from Click into the async application boundary."""

    display: str | None = None
    no_update_check: bool = False
    setup_advanced: bool = False
    command: str | None = None
    action: str | None = None
    config_path: Path | None = None
    data_root: Path | None = None
    output_format: OutputFormat = OutputFormat.text
    prompt: str | None = None
    thread_id: str | None = None
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
    provider: str | None = None
    account_source: str | None = None
    account_id: str | None = None
    allow_account_switch: bool = False
    device_code: bool = False
    credential_key: SecretStr | None = field(default=None, repr=False)
    web_host: str = "127.0.0.1"
    web_port: int = 8765
    web_api_key: str | None = field(default=None, repr=False)
    dangerously_bypass_permission: bool = False
    share_computer: bool = True


@dataclass(frozen=True, slots=True)
class _CliContext:
    display: str | None
    no_update_check: bool
    config_path: Path | None
    data_root: Path | None
    thread_id: str | None
    agent_id: str | None
    environment_mode: str | None
    environment_profile_id: str | None


_CONTEXT_SETTINGS = {"help_option_names": ("-h", "--help")}
_FORMAT_CHOICE = click.Choice(tuple(item.value for item in OutputFormat), case_sensitive=True)
_ENVIRONMENT_MODE_CHOICE = click.Choice(("full-control", "sandbox"))
_PROVIDER_CHOICE = click.Choice(("codex", "grok", "copilot"))
_PRODUCT_CHOICE = click.Choice(("claude-code", "cursor", "codex"))
_SCOPE_CHOICE = click.Choice(("user", "project"))
_PATH = click.Path(path_type=Path)


@click.group(
    name="a13n-harness-ui",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings=_CONTEXT_SETTINGS,
)
@click.option(
    "--config",
    "config_path",
    type=_PATH,
    help="Explicit Harness UI configuration YAML (default: ~/.a13n-harness-ui/a13n-harness-ui.yaml).",
)
@click.option("--data-root", type=_PATH, help="Override the local Harness UI data root.")
@click.option("--resume", "thread_id", help="Resume a saved session by ID.")
@click.option("--agent", "agent_id", help="Select a configured Agent for this session.")
@click.option(
    "--environment-mode",
    type=_ENVIRONMENT_MODE_CHOICE,
    help="Override the built-in Environment mode for this session.",
)
@click.option(
    "--environment-profile",
    "environment_profile_id",
    help="Override the custom Environment profile for this session.",
)
@click.option(
    "--display",
    type=click.Choice(("concise", "detailed")),
    default=None,
    help="Presentation only; overrides display.mode (default concise). /mode switches live.",
)
@click.option("--no-update-check", is_flag=True, help="Skip startup update detection for this invocation.")
@click.version_option(package_name="a13n-harness-ui")
@click.pass_context
def cli(
    ctx: click.Context,
    display: str | None,
    no_update_check: bool,
    config_path: Path | None,
    data_root: Path | None,
    thread_id: str | None,
    agent_id: str | None,
    environment_mode: str | None,
    environment_profile_id: str | None,
) -> None:
    """An interactive coding agent in your terminal.

    Start in the current directory. Use /help inside a session.
    Enter submits; Alt+Enter inserts a newline; Ctrl+D exits.
    Configuration: ~/.a13n-harness-ui/a13n-harness-ui.yaml. Use `a13n-harness-ui webui` for the browser UI."""

    ctx.obj = _CliContext(
        display=display,
        no_update_check=no_update_check,
        config_path=config_path,
        data_root=data_root,
        thread_id=thread_id,
        agent_id=agent_id,
        environment_mode=environment_mode,
        environment_profile_id=environment_profile_id,
    )
    _validate_environment_selection(environment_mode, environment_profile_id)
    if ctx.invoked_subcommand is not None:
        return
    _execute(
        CliRequest(
            display=display,
            no_update_check=no_update_check,
            config_path=config_path,
            data_root=data_root,
            thread_id=thread_id,
            agent_id=agent_id,
            environment_mode=environment_mode,
            environment_profile_id=environment_profile_id,
        )
    )


@cli.command("webui")
@click.option("--host", default="127.0.0.1", show_default=True, help="Listener IPv4 or IPv6 address.")
@click.option("--port", type=click.IntRange(1, 65535), default=8765, show_default=True)
@click.option(
    "--apikey",
    "--api-key",
    "api_keys",
    multiple=True,
    help="Listener API key; overrides A13N_HARNESS_UI_API_KEY (visible in shell arguments).",
)
@click.option(
    "--dangerous-skip-permissions",
    "--dangerously-bypass-permission",
    "dangerously_bypass_permission",
    is_flag=True,
    help="Disable Web authentication only; does not change Agent permissions.",
)
@click.option(
    "--share-computer/--no-share-computer",
    default=True,
    show_default=True,
    help="Share native Host Files as the server OS account, independent of Agent permissions.",
)
@click.pass_context
def webui_command(
    ctx: click.Context,
    host: str,
    port: int,
    api_keys: tuple[str, ...],
    dangerously_bypass_permission: bool,
    share_computer: bool,
) -> None:
    """Run one foreground WebUI server with bundled browser assets."""
    if len(set(api_keys)) > 1:
        raise click.UsageError("Conflicting --apikey/--api-key values cannot be combined.")
    api_key = api_keys[0] if api_keys else None
    _execute(
        _request(
            ctx,
            command="webui",
            web_host=host,
            web_port=port,
            web_api_key=api_key,
            dangerously_bypass_permission=dangerously_bypass_permission,
            share_computer=share_computer,
        )
    )


@cli.command("update")
def update_command() -> None:
    """Update this uv-tool installation now, without starting chat or setup."""
    from a13n_harness_ui.updater import update

    update()


@cli.command("setup")
@click.option(
    "--advanced", is_flag=True, help="Also choose context, reasoning, tool review, subagents, and instructions."
)
@click.pass_context
def setup_command(ctx: click.Context, advanced: bool) -> None:
    """Configure a model, context budget, and execution permissions interactively."""
    _execute(_request(ctx, command="setup", setup_advanced=advanced))


@cli.group("add")
def add_group() -> None:
    """Add to your local configuration."""


@add_group.command("agent")
@click.option("--advanced", is_flag=True, help="Also customize reasoning, tool review, and instructions.")
@click.pass_context
def add_agent_command(ctx: click.Context, advanced: bool) -> None:
    """Create another agent without changing existing agents or defaults."""
    _execute(_request(ctx, command="add", action="agent", setup_advanced=advanced))


@add_group.command("model")
@click.option("--advanced", is_flag=True, help="Also customize subscription context and reasoning settings.")
@click.pass_context
def add_model_command(ctx: click.Context, advanced: bool) -> None:
    """Create a reusable model without creating or changing an agent."""
    _execute(_request(ctx, command="add", action="model", setup_advanced=advanced))


@cli.command("run")
@click.argument("prompt")
@click.option("--resume", "thread_id", help="Continue a saved session.")
@click.option("--agent", "agent_id", help="Agent used for a new session.")
@click.option(
    "--environment-mode",
    type=_ENVIRONMENT_MODE_CHOICE,
    help="Built-in execution mode used for a new session.",
)
@click.option(
    "--environment-profile",
    "environment_profile_id",
    help="Custom Environment profile used for a new session.",
)
@click.option("--title", help="Title used for a new session.")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def run_command(
    ctx: click.Context,
    prompt: str,
    thread_id: str | None,
    agent_id: str | None,
    environment_mode: str | None,
    environment_profile_id: str | None,
    title: str | None,
    output_format: str,
) -> None:
    """Execute one prompt without an interactive terminal."""

    _validate_environment_selection(environment_mode, environment_profile_id)
    _execute(
        _request(
            ctx,
            command="run",
            prompt=prompt,
            thread_id=thread_id,
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


@config_group.command("subagents")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def config_subagents_command(ctx: click.Context, output_format: str) -> None:
    """List package-owned subagents and their current inclusion."""

    _execute(_request(ctx, command="config", action="subagents", output_format=OutputFormat(output_format)))


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


@cli.group("environment")
def environment_group() -> None:
    """Query Environment modes and profiles."""


@environment_group.command("list")
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text", show_default=True)
@click.pass_context
def environment_list_command(ctx: click.Context, output_format: str) -> None:
    """List Environment modes and profiles."""

    _execute(_request(ctx, command="environment", action="list", output_format=OutputFormat(output_format)))


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
    from pydantic import SecretStr, ValidationError

    from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput

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


@cli.command("login")
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


@auth_group.command("sources")
@click.argument("provider", type=_PROVIDER_CHOICE)
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text")
@click.pass_context
def auth_sources_command(ctx: click.Context, provider: str, output_format: str) -> None:
    """List supported saved account sources without exposing credentials."""
    _execute(
        _request(ctx, command="auth", action="sources", provider=provider, output_format=OutputFormat(output_format))
    )


@auth_group.command("select")
@click.argument("provider", type=_PROVIDER_CHOICE)
@click.option("--source", "account_source", required=True, type=click.Choice(("native", "copilot_cli_file")))
@click.option("--account", "account_id", required=True)
@click.option("--format", "output_format", type=_FORMAT_CHOICE, default="text")
@click.pass_context
def auth_select_command(
    ctx: click.Context, provider: str, account_source: str, account_id: str, output_format: str
) -> None:
    """Explicitly replace this Host's account and credential-source binding."""
    _execute(
        _request(
            ctx,
            command="auth",
            action="select",
            provider=provider,
            account_source=account_source,
            account_id=account_id,
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
        raise RuntimeError("Harness UI CLI context is unavailable")
    return root


def _request(ctx: click.Context, **values: Any) -> CliRequest:
    root = _root_context(ctx)
    options: dict[str, Any] = {
        "config_path": root.config_path,
        "data_root": root.data_root,
        "display": root.display,
        "no_update_check": root.no_update_check,
        "thread_id": root.thread_id,
        "agent_id": root.agent_id,
        "environment_mode": root.environment_mode,
        "environment_profile_id": root.environment_profile_id,
    }
    options.update({key: value for key, value in values.items() if value is not None})
    return CliRequest(**options)


def _validate_environment_selection(mode: str | None, profile_id: str | None) -> None:
    if mode is not None and profile_id is not None:
        raise click.UsageError("--environment-mode cannot be combined with --environment-profile.")
    from a13n_harness_ui.environment_profiles import environment_profile_id_for_mode, require_supported_local_profile

    try:
        require_supported_local_profile(environment_profile_id_for_mode(mode) if mode is not None else profile_id)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc


def _execute(request: CliRequest) -> None:
    _validate_environment_selection(request.environment_mode, request.environment_profile_id)
    if request.thread_id is not None and any(
        value is not None
        for value in (
            request.agent_id,
            request.environment_mode,
            request.environment_profile_id,
            request.title,
        )
    ):
        raise click.UsageError(
            "--resume cannot be combined with Agent, Environment, or title overrides. Resume first, then use /environment explicitly."
        )
    from a13n_harness_ui.errors import HarnessUiError

    if request.command in {None, "setup", "add"}:
        from a13n_harness_ui.terminal import start

        start(request)
        return
    from a13n_harness_ui.cli_runtime import _run

    try:
        exit_code = asyncio.run(_run(request))
    except KeyboardInterrupt as exc:
        if request.command == "auth" and request.action == "login":
            raise click.exceptions.Exit(130) from exc
        return
    except HarnessUiError as exc:
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
        exit_code = cli.main(
            args=None if argv is None else list(argv), prog_name="a13n-harness-ui", standalone_mode=False
        )
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
