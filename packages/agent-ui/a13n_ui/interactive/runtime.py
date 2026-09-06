"""Lazy startup boundary. Import this module off the terminal event loop."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness.model_auth import CodexCredentials, GrokCredentials
from a13n_logging import LogFormat, configure_logging
from anyio import fail_after

from a13n_ui.app import open_agent_ui_app
from a13n_ui.cli import CliRequest
from a13n_ui.model_accounts.codex import CodexLoginRequest
from a13n_ui.model_accounts.grok import GrokLoginRequest
from a13n_ui.model_accounts.login import authorize_codex, authorize_grok
from a13n_ui.settings_loader import ensure_default_directories, load_agent_ui_settings

from .backend import SessionBackend
from .rendering import Status


@asynccontextmanager
async def open_session(
    request: CliRequest, directory: Path, status: Status, emit: Callable[[str], None]
) -> AsyncIterator[SessionBackend]:
    source = await load_agent_ui_settings(request.config_path, data_root=request.data_root)
    await asyncio.to_thread(ensure_default_directories, source)
    configure_logging(
        level=source.settings.log_level, log_format=LogFormat(source.settings.log_format), logger_names=("a13n_ui",)
    )

    def present(**values: object) -> None:
        for key in ("verification_url", "user_code", "message"):
            if values.get(key):
                emit(str(values[key]))
        emit("Waiting for authorization. Use /cancel to stop.")

    async def codex_login(request: CodexLoginRequest) -> CodexCredentials:
        with fail_after(900):
            return await authorize_codex(request, "device", present)

    async def grok_login(request: GrokLoginRequest) -> GrokCredentials:
        with fail_after(900):
            return await authorize_grok(request, "device", present)

    async with open_agent_ui_app(
        source.settings,
        configuration_path=source.path,
        configuration_error=source.candidate_error,
        codex_login=codex_login,
        grok_login=grok_login,
    ) as app:
        yield SessionBackend(app, request, directory, status)
