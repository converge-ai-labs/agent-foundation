"""Startup update detection and explicit installer handoff; never background installation."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx2
from packaging.version import InvalidVersion, Version

from a13n_harness_ui.updater import update_command


@dataclass(frozen=True, slots=True)
class AvailableUpdate:
    current: str
    latest: str


def installed_version() -> str | None:
    try:
        current = version("a13n-harness-ui")
        parsed = Version(current)
        return None if parsed.base_version == "0.0.0" or parsed.is_devrelease else current
    except (PackageNotFoundError, InvalidVersion):
        return None


def available_update(current: str, latest: str) -> AvailableUpdate | None:
    try:
        candidate = Version(latest)
        if candidate.is_prerelease or candidate.is_devrelease or candidate <= Version(current):
            return None
    except InvalidVersion:
        return None
    return AvailableUpdate(current, str(candidate))


async def check_update(root: Path, *, current: str | None = None) -> AvailableUpdate | None:
    """Read public package metadata with a daily cache and a bounded startup wait."""
    current = current or installed_version()
    if current is None:
        return None
    cache = root / "cache" / "terminal-update.json"
    try:
        with cache.open("rb") as stream:
            cached = json.loads(stream.read(8192))
        if isinstance(cached, dict) and 0 <= time.time() - float(cached["checked_at"]) < 86400:
            return available_update(current, str(cached["latest"]))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    # Keep terminal startup imports independent of execution dependencies.
    from a13n_harness.http import outbound_tls_verify

    try:
        async with asyncio.timeout(3), httpx2.AsyncClient(verify=outbound_tls_verify(), timeout=2) as client:
            async with client.stream("GET", "https://pypi.org/pypi/a13n-harness-ui/json") as response:
                response.raise_for_status()
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        return None
            payload = json.loads(body)
            latest = str(payload["info"]["version"])
            Version(latest)
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(f".{os.getpid()}.tmp")
        try:
            temporary.write_text(json.dumps({"checked_at": time.time(), "latest": latest}))
            temporary.replace(cache)
        finally:
            temporary.unlink(missing_ok=True)
        return available_update(current, latest)
    except (TimeoutError, httpx2.HTTPError, OSError, ValueError, KeyError, TypeError):
        return None


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
