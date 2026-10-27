"""macOS browser launch environment and optional native preview preparation."""

from __future__ import annotations

import asyncio
import os
from platform import mac_ver
from typing import Any, cast


def preview_available() -> bool:
    if int(mac_ver()[0].split(".")[0] or 0) < 14:
        return False
    try:
        import Quartz  # pyright: ignore[reportMissingImports]

        return bool(cast(Any, Quartz).CGPreflightScreenCaptureAccess())
    except (ImportError, OSError):
        return False


def session_available() -> bool:
    import Quartz  # pyright: ignore[reportMissingImports]

    quartz = cast(Any, Quartz)
    session = quartz.CGSessionCopyCurrentDictionary()
    return bool(session and session.get(quartz.kCGSessionOnConsoleKey) and not session.get("CGSSessionScreenIsLocked"))


async def open_capture_settings() -> None:
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/open",
        "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(5):
            if await process.wait():
                raise OSError("Could not open Screen Recording settings")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class MacDesktop:
    """Launch on the logged-in Mac; no display server or remote input channel."""

    wayland = False
    resize_available = False
    window_manager = True
    chromium_arguments: tuple[str, ...] = ()
    password = readonly_password = ""

    def __init__(self) -> None:
        self.env = dict(os.environ)
        self.processes: list[tuple[asyncio.subprocess.Process, str]] = []

    async def start(self) -> None:
        pass

    async def settle_input(self) -> None:
        pass

    async def close(self) -> None:
        pass
