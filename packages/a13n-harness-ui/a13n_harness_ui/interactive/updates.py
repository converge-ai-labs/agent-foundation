"""Startup update detection and explicit installer handoff; never background installation."""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx2
from packaging.version import InvalidVersion, Version

from a13n_harness_ui.updater import UpdateCommand, update_command

from .onboarding import LandingScreen, SetupBack, SetupCancelled
from .selection import Choice, Selection, resolve_choice
from .setup import Question


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


async def prompt_update(root: Path, landing: LandingScreen) -> UpdateCommand | None:
    """Return an installer command only after this launch's explicit confirmation."""
    landing.title = "Harness UI · Update"
    landing.emit("Checking for updates…")
    try:
        update = await check_update(root)
        if update is None:
            return None
        command = update_command()
        details = (
            f"Run: uv tool upgrade a13n-harness-ui\nTool directory: {command.tool_directory}\n"
            "The terminal will close before installation. Restart a13n-harness-ui afterwards."
            if command is not None
            else "This installation has no recognized updater. Update with its original package manager.\n"
            "For uv-tool installations: uv tool upgrade a13n-harness-ui"
        )
        notice = f"Update available: a13n-harness-ui {update.current} → {update.latest}\n{details}"
        landing.emit(notice)
        choices = (
            *((Choice("update", "Update now", "Run the displayed command, then exit"),) if command else ()),
            Choice("later", "Not now", "Continue startup; ask again next time a newer version is available"),
        )
        values = tuple(choice.value for choice in choices)
        while True:
            try:
                answer = await landing.ask(
                    Question("update", "Install this update?" if command else "Continue startup?", "later", values),
                    Selection(choices, cursor=len(choices) - 1),
                )
            except (SetupBack, SetupCancelled):
                return None
            try:
                selected = resolve_choice(answer, values)
                if selected not in values:
                    raise ValueError("Choose one of the displayed options.")
            except ValueError as exc:
                landing.emit(f"{notice}\n{exc}")
                continue
            if selected == "update":
                return command
            if selected == "later":
                return None
    finally:
        landing.title = "Harness UI · Startup"
        landing.emit("Checking local configuration…")
