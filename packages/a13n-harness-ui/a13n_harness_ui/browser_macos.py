"""Attach to Apple's existing Screen Sharing desktop without owning the service."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from pydantic import Field, SecretStr, field_validator

from a13n_harness_ui.browser_vnc import VncConnection, password_bytes, screen_sharing
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.model_accounts.auth import HostAuthDocument
from a13n_harness_ui.surfaces import SurfaceModel

PASSWORD_ENV = "A13N_HARNESS_UI_MACOS_VNC_PASSWORD"


class MacVncPasswordInput(SurfaceModel):
    password: SecretStr = Field(repr=False, min_length=1, max_length=8)

    @field_validator("password")
    @classmethod
    def valid_password(cls, value: SecretStr) -> SecretStr:
        password_bytes(value.get_secret_value())
        return value


class MacVncCredentials:
    """Screen Sharing credentials in the shared, owner-only Host auth document."""

    def __init__(self, path: Path) -> None:
        self.document = HostAuthDocument(path)

    async def load(self) -> str:
        saved = (await self.document.read()).get("macos_vnc_password")
        if saved is None:
            return os.environ.get(PASSWORD_ENV, "")
        if not isinstance(saved, str):
            raise HarnessUiError("Invalid saved VNC credential.", code="browser_credential_invalid")
        return saved

    async def save(self, password: str) -> None:
        password_bytes(password)
        await self.document.update(lambda document: document.update(macos_vnc_password=password))


async def open_sharing_settings() -> None:
    process = await asyncio.create_subprocess_exec(
        "/usr/bin/open",
        "x-apple.systempreferences:com.apple.Sharing-Settings.extension",
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        async with asyncio.timeout(5):
            if await process.wait():
                raise OSError("Could not open Sharing settings")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def screen_sharing_available() -> bool:
    try:
        async with asyncio.timeout(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", 5900)
            try:
                return await reader.readexactly(12) in {b"RFB 003.008\n", b"RFB 003.889\n"}
            finally:
                writer.close()
                await writer.wait_closed()
    except (OSError, TimeoutError, asyncio.IncompleteReadError):
        return False


def password_ready(password: str | None = None) -> bool:
    try:
        password_bytes(os.environ.get(PASSWORD_ENV, "") if password is None else password)
    except ValueError:
        return False
    return True


class MacDesktop:
    """The physical macOS desktop; only viewer connections belong to Harness UI."""

    wayland = False
    resize_available = False
    window_manager = True
    chromium_arguments: tuple[str, ...] = ()
    password = readonly_password = ""

    def __init__(self, password: str | None = None) -> None:
        self.env = dict(os.environ)
        environment_password = self.env.pop(PASSWORD_ENV, "")
        self._password = environment_password if password is None else password
        self.processes: list[tuple[asyncio.subprocess.Process, str]] = []

    def set_password(self, password: str) -> None:
        self._password = password

    async def start(self) -> None:
        connection = await self.connect(controlling=False)
        connection.close()
        await connection.wait_closed()

    async def connect(self, *, controlling: bool) -> VncConnection:
        return await screen_sharing(self._password, controlling=controlling)

    async def clipboard(self, text: str | None) -> str:
        """Use the native UTF-8 pasteboard; classic RFB clipboard is Latin-1 only."""
        process = await asyncio.create_subprocess_exec(
            "/usr/bin/pbpaste" if text is None else "/usr/bin/pbcopy",
            stdin=asyncio.subprocess.PIPE if text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**self.env, "LC_ALL": "en_US.UTF-8"},
        )
        try:
            async with asyncio.timeout(5):
                if text is not None:
                    await process.communicate(text.encode("utf-8"))
                    result = text
                else:
                    assert process.stdout is not None
                    try:
                        content = await process.stdout.readexactly(262145)
                    except asyncio.IncompleteReadError as exc:
                        content = exc.partial
                    if len(content) > 262144:
                        raise OSError("Clipboard text exceeds 256 KiB")
                    await process.wait()
                    result = content.decode("utf-8")
                if process.returncode:
                    raise OSError("macOS clipboard access failed")
                return result
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    async def settle_input(self) -> None:
        # HostBrowsers closes and awaits every connection before handback.
        pass

    async def close(self) -> None:
        self._password = ""
