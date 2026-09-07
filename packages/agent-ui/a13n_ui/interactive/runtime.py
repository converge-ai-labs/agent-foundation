"""Lazy startup boundary. Import this module off the terminal event loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness.model_auth import CodexCredentials, GrokCredentials
from anyio import fail_after

from a13n_ui.app import open_agent_ui_app
from a13n_ui.cli import CliRequest
from a13n_ui.model_accounts.codex import CodexLoginRequest
from a13n_ui.model_accounts.grok import GrokLoginRequest
from a13n_ui.model_accounts.login import authorize_codex, authorize_grok
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings, resolve_agent_ui_data_root

from .backend import SessionBackend
from .lifecycle import check_update, terminal_logging
from .rendering import Status


@asynccontextmanager
async def open_session(
    request: CliRequest, directory: Path, status: Status, emit: Callable[[str], None]
) -> AsyncIterator[SessionBackend]:
    root = resolve_agent_ui_data_root(request.config_path, data_root=request.data_root)
    with terminal_logging(root, "INFO", emit):
        async with _session(request, directory, status, emit) as backend:
            yield backend


@asynccontextmanager
async def _session(
    request: CliRequest, directory: Path, status: Status, emit: Callable[[str], None]
) -> AsyncIterator[SessionBackend]:
    source = await load_agent_ui_settings(request.config_path, data_root=request.data_root)
    logging.getLogger().setLevel(source.settings.log_level)
    await asyncio.to_thread(ensure_default_directories, source)

    def present(**values: object) -> None:
        for key in ("verification_url", "user_code", "message"):
            if values.get(key):
                emit(str(values[key]))
        emit("Waiting for authorization. Ctrl+C cancels.")

    async def codex_login(request: CodexLoginRequest) -> CodexCredentials:
        with fail_after(900):
            return await authorize_codex(request, "device", present)

    async def grok_login(request: GrokLoginRequest) -> GrokCredentials:
        with fail_after(900):
            return await authorize_grok(request, "device", present)

    async def notify_update() -> None:
        notice = await check_update(source.settings.storage.data_root)
        if notice:
            status.update_notice = notice
            emit(notice)

    update = (
        asyncio.create_task(notify_update())
        if source.settings.terminal_update_check and request.command != "setup"
        else None
    )
    try:
        async with open_agent_ui_app(
            source.settings,
            configuration_path=source.path,
            configuration_error=source.candidate_error,
            codex_login=codex_login,
            grok_login=grok_login,
        ) as app:
            yield SessionBackend(app, request, directory, status)
    finally:
        if update is not None:
            update.cancel()
            await asyncio.gather(update, return_exceptions=True)
