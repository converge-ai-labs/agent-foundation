"""Startup update detection and explicit installer handoff; never background installation."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from a13n_harness_ui import __version__
from a13n_harness_ui.updater import update_command


@dataclass(frozen=True, slots=True)
class AvailableUpdate:
    current: str
    latest: str


async def check_update(root: Path, *, current: str | None = None) -> AvailableUpdate | None:
    """Reuse the shared release cache with the terminal's startup deadline."""
    from a13n_harness_ui.update_notices import UpdateChecker

    current = current or __version__
    release = await UpdateChecker(root, current).check_release()
    return AvailableUpdate(current, release.version) if release is not None and release.is_newer else None


async def notify_update(root: Path, chat_ready: asyncio.Event, emit: Callable[[str], None]) -> None:
    """Check without delaying startup; notify only after entering chat."""
    try:
        update = await check_update(root)
        if update is None:
            return
        await chat_ready.wait()
        instruction = (
            "To update, exit Harness UI and run: a13n-harness-ui update"
            if update_command() is not None
            else "Update with this installation's original package manager."
        )
        emit(f"Update available: a13n-harness-ui {update.current} → {update.latest}\n{instruction}")
    except Exception as exc:
        # Optional update detection must never interrupt a working conversation.
        logging.getLogger(__name__).warning("Background update check failed (%s)", type(exc).__name__)
