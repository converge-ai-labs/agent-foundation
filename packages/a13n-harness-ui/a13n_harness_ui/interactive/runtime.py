"""Lazy startup boundary. Import this module off the terminal event loop."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from a13n_harness_ui.app import open_harness_ui_app
from a13n_harness_ui.cli import CliRequest
from a13n_harness_ui.settings_loader import (
    ensure_default_directories,
    load_harness_ui_settings,
    resolve_harness_ui_data_root,
)

from .backend import SessionBackend
from .lifecycle import terminal_logging
from .rendering import Status


@asynccontextmanager
async def open_session(
    request: CliRequest, directory: Path, status: Status, emit: Callable[[str], None]
) -> AsyncIterator[SessionBackend]:
    root = resolve_harness_ui_data_root(request.config_path, data_root=request.data_root)
    with terminal_logging(root, "INFO", emit):
        async with _session(request, directory, status, emit) as backend:
            yield backend


@asynccontextmanager
async def _session(
    request: CliRequest, directory: Path, status: Status, emit: Callable[[str], None]
) -> AsyncIterator[SessionBackend]:
    source = await load_harness_ui_settings(request.config_path, data_root=request.data_root)
    logging.getLogger().setLevel(source.settings.log_level)
    await asyncio.to_thread(ensure_default_directories, source)

    async with open_harness_ui_app(
        source.settings,
        configuration_path=source.path,
        configuration_error=source.candidate_error,
    ) as app:
        yield SessionBackend(app, request, directory, status)
