"""Lazy application handlers for the terminal executable."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

import click
from a13n_harness.model_auth import CodexLoginResult, GrokCredentials
from a13n_logging import LogFormat, configure_logging
from anyio import fail_after
from pydantic import BaseModel

from a13n_harness_ui.app import HarnessUiApp, open_harness_ui_app
from a13n_harness_ui.cli import CliRequest, OutputFormat
from a13n_harness_ui.configuration import (
    LoadedHarnessUiConfiguration,
)
from a13n_harness_ui.content_plugins import ContentPluginStore, InstalledContentPlugin
from a13n_harness_ui.errors import ConfigurationError
from a13n_harness_ui.model_accounts import (
    GrokLoginRequest,
    Provider,
)
from a13n_harness_ui.settings_loader import ensure_default_directories, load_harness_ui_settings
from a13n_harness_ui.surfaces import (
    RootOperationStatus,
    RootOperationView,
)


async def _run(request: CliRequest) -> int:
    source = await load_harness_ui_settings(request.config_path, data_root=request.data_root)
    await asyncio.to_thread(ensure_default_directories, source)
    settings = source.settings
    configure_logging(
        level=settings.log_level,
        log_format=LogFormat(settings.log_format),
        logger_names=("a13n_harness_ui",),
    )
    if request.command == "plugin":
        return await _run_content_plugins(request, settings.storage.data_root)
    use_device_code = request.device_code

    def app_factory() -> AbstractAsyncContextManager[HarnessUiApp]:
        return open_harness_ui_app(
            settings,
            configuration_path=source.path,
            configuration_error=source.candidate_error,
            host_mode="webui" if request.command == "webui" else "local",
            codex_login=(
                None
                if request.command == "webui"
                else lambda request: _codex_cli_login(request, device_code=use_device_code)
            ),
            grok_login=(
                None
                if request.command == "webui"
                else lambda request: _grok_cli_login(request, device_code=use_device_code)
            ),
        )

    if request.command == "webui":
        from a13n_harness_ui.webui import run

        await run(
            app_factory,
            host=request.web_host,
            port=request.web_port,
            api_key=request.web_api_key,
            dangerously_bypass_permission=request.dangerously_bypass_permission,
        )
        return 0

    async with app_factory() as app:
        if request.command == "run":
            return await _run_one_shot(app, request)
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


def _present_login(**values: object) -> None:
    if values.get("verification_url"):
        click.echo(f"Open this URL to authenticate:\n{values['verification_url']}", err=True)
    if values.get("user_code"):
        click.echo(f"Confirm this code in your browser: {values['user_code']}", err=True)
    if values.get("message"):
        click.echo(values["message"], err=True)
    click.echo("Waiting for authorization (Ctrl+C to cancel)...", err=True)


async def _codex_cli_login(request: object, *, device_code: bool = True) -> CodexLoginResult:
    from a13n_harness_ui.model_accounts.codex import CodexLoginRequest
    from a13n_harness_ui.model_accounts.login import authorize_codex

    if not isinstance(request, CodexLoginRequest):
        raise TypeError("Codex login requires CodexLoginRequest")
    with fail_after(900):
        return await authorize_codex(request, "device" if device_code else "browser", _present_login)


async def _grok_cli_login(request: object, *, device_code: bool = True) -> GrokCredentials:
    from a13n_harness_ui.model_accounts.login import authorize_grok

    if not isinstance(request, GrokLoginRequest):
        raise TypeError("Grok login requires GrokLoginRequest")
    with fail_after(900):
        return await authorize_grok(request, "device" if device_code else "browser", _present_login)


async def _run_management(
    app: HarnessUiApp,
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
        if request.action == "subagents":
            from a13n_harness_ui.configuration.loader import parse_canonical_markdown
            from a13n_harness_ui.subagents import builtin_subagent_sources

            configuration = await _require_configuration(app)
            included = configuration.document.subagents.include
            catalog = [
                parse_canonical_markdown(Path(f"{name}.md"), content) for name, content in builtin_subagent_sources()
            ]
            _print_projection(
                {
                    "configuration_key": "subagents.include",
                    "subagents": [
                        {
                            "name": child.name,
                            "id": child.id,
                            "description": child.description,
                            "included": child.name in included,
                        }
                        for child in catalog
                    ],
                },
                request.output_format,
            )
            return 0
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

    if request.command == "environment":
        _print_projection({"environment_profiles": await app.environment_profiles()}, request.output_format)
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
        raise RuntimeError("Unsupported Harness UI CLI command")
    if request.action.startswith("key-"):
        from a13n_harness_ui.model_accounts.api_keys import ApiKeyInput

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


async def _require_configuration(app: HarnessUiApp) -> LoadedHarnessUiConfiguration:
    configuration = await app.current_configuration()
    if configuration is None:
        raise ConfigurationError(
            "No accepted Harness UI configuration is available.",
            code="configuration_unavailable",
        )
    return configuration


async def _run_one_shot(
    app: HarnessUiApp,
    request: CliRequest,
) -> int:
    if request.prompt is None:
        raise RuntimeError("Headless Run requires a prompt")
    from a13n_harness_ui.interactive.backend import SessionBackend
    from a13n_harness_ui.interactive.rendering import Status

    backend = SessionBackend(app, request, Path.cwd(), Status())
    await backend.initialize()
    thread_id = await backend.ensure_session()

    receipt = await app.submit_thread(thread_id=thread_id, prompt=request.prompt, model_overrides=backend.overrides)
    operation = await app.wait_root_operation(receipt.receipt_id)
    if request.output_format is OutputFormat.json:
        click.echo(operation.model_dump_json())
    else:
        _print_text_result(operation)
    return 0 if operation.status is RootOperationStatus.completed else 1


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
