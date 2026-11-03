"""Shared allocation fencing and access for the two built-in Linux runtimes."""

import asyncio
import fcntl
import json
import re
import secrets
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from urllib.parse import urlencode

import anyio
from a13n_harness.capabilities.browser.cdp import Chromium
from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_service.providers.browser.definition import BrowserProviderError, LiveBrowser, ViewerGrant
from a13n_service.providers.environment.desktop_guest import private_directory, publish


class Recipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    width: int = Field(default=1280, ge=640, le=3840)
    height: int = Field(default=800, ge=480, le=2160)


async def blocking(function, *args, **kwargs):
    # A cancelled Engine/filesystem mutation must finish before its allocation lock is released.
    return await to_thread.run_sync(partial(function, *args, **kwargs))


@dataclass
class Allocation:
    id: str
    root: Path
    record: dict

    async def save(self) -> None:
        await blocking(publish, self.root / "allocation.json", json.dumps(self.record))


class SelfHosted(ABC):
    def __init__(self, root: Path, backend: str):
        self.root, self.backend = root, backend

    @asynccontextmanager
    async def allocation(self, identity: str) -> AsyncIterator[Allocation]:
        if not re.fullmatch(r"browser_[a-zA-Z0-9_]+", identity):
            raise BrowserProviderError("invalid_allocation", certainty="not_dispatched")
        root = self.root / identity
        await blocking(private_directory, self.root)
        await blocking(private_directory, root)
        lock = await blocking((root / "allocation.lock").open, "a")
        try:
            while True:
                try:
                    await blocking(fcntl.flock, lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            with anyio.CancelScope(shield=True):
                path = root / "allocation.json"
                record = json.loads(await blocking(path.read_text)) if await blocking(path.exists) else {}
                if record and record["backend"] != self.backend:
                    raise BrowserProviderError("allocation_backend_changed", certainty="known")
                yield Allocation(identity, root, record)
        finally:
            await blocking(lock.close)

    def available(self, allocation: Allocation) -> None:
        if allocation.record.get("deleted"):
            raise BrowserProviderError("allocation_deleted", certainty="known")

    async def create(self, allocation_id: str, recipe: BaseModel) -> LiveBrowser:
        async with self.allocation(allocation_id) as allocation:
            self.available(allocation)
            if allocation.record:
                live = await self.observe(allocation)
                if live is None:
                    raise BrowserProviderError("allocation_create_unresolved", certainty="known")
                return live
            allocation.record = {"backend": self.backend, "generation": 1, "recipe": recipe.model_dump(mode="json")}
            await allocation.save()
            try:
                await self.provision(allocation)
            except BrowserProviderError as error:
                if error.certainty == "not_dispatched":
                    allocation.record.clear()
                    await allocation.save()
                raise
            return await self.launch(allocation)

    async def inspect(self, allocation_id: str, state: dict[str, JsonValue] | None) -> LiveBrowser | None:
        async with self.allocation(allocation_id) as allocation:
            if not allocation.record or allocation.record.get("deleted"):
                return None
            return await self.observe(allocation)

    async def start(self, allocation_id: str, state: dict[str, JsonValue], *, generation: int) -> LiveBrowser:
        async with self.allocation(allocation_id) as allocation:
            self.available(allocation)
            if not allocation.record:
                raise BrowserProviderError("allocation_missing", certainty="known")
            previous = allocation.record["generation"]
            if generation <= previous:
                live = await self.observe(allocation)
                if generation != previous or live is None or live.status != "ready":
                    raise BrowserProviderError("runtime_start_fenced", certainty="known")
                return live
            current = await self.observe(allocation)
            if current is None or current.status != "stopped":
                raise BrowserProviderError("runtime_not_stopped", certainty="not_dispatched")
            allocation.record["generation"] = generation
            await allocation.save()
            try:
                return await self.launch(allocation)
            except BrowserProviderError as error:
                if error.certainty == "not_dispatched":
                    allocation.record["generation"] = previous
                    await allocation.save()
                raise

    async def stop(self, allocation_id: str, state: dict[str, JsonValue]) -> LiveBrowser:
        async with self.allocation(allocation_id) as allocation:
            self.available(allocation)
            current = await self.observe(allocation)
            if current is None:
                raise BrowserProviderError("allocation_missing", certainty="known")
            if current.runtime_id != state.get("runtime_id"):
                raise BrowserProviderError("runtime_changed", certainty="known")
            await self.halt(allocation)
            result = await self.observe(allocation)
            if result is None or result.status != "stopped":
                raise BrowserProviderError("runtime_stop_unconfirmed")
            return result

    async def delete(self, allocation_id: str, state: dict[str, JsonValue] | None) -> None:
        async with self.allocation(allocation_id) as allocation:
            # Persist the fence before external cleanup; retries continue that same cleanup.
            allocation.record.update(backend=self.backend, deleted=True)
            await allocation.save()
            await self.destroy(allocation)

    @asynccontextmanager
    async def running(self, state: dict[str, JsonValue]) -> AsyncIterator[tuple[Allocation, LiveBrowser]]:
        async with self.allocation(str(state["allocation_id"])) as allocation:
            self.available(allocation)
            current = await self.observe(allocation)
            if current is None or current.status != "ready" or current.runtime_id != state["runtime_id"]:
                raise BrowserProviderError("runtime_changed", certainty="known")
            yield allocation, current

    async def connect(self, state: dict[str, JsonValue]) -> Chromium:
        async with self.running(state) as (allocation, current):
            browser = await Chromium.connect(str(current.state["cdp_url"]), timeout=15, discover=False)
            try:
                await browser.configure_downloads(self.download_directory(allocation))
            except BaseException:
                await browser.close()
                raise
            return browser

    async def ensure_viewer(
        self, state: dict[str, JsonValue], grant_id: str, *, episode: int, expires_at: datetime
    ) -> ViewerGrant:
        async with self.running(state) as (allocation, current):
            result = await self.desktop(
                allocation,
                {
                    "protocol_version": 2,
                    "generation": current.state["viewer_generation"],
                    "operation": "ensure",
                    "id": grant_id,
                    "episode": episode,
                    "expires_at": expires_at.timestamp(),
                },
            )
            return ViewerGrant(
                str(current.state["viewer_url"]) + "?" + urlencode({"token": result["token"]}),
                {"password": result["password"], "protocol": "vnc"},
                datetime.fromtimestamp(result["expires_at"], UTC),
            )

    async def revoke_viewer(self, state: dict[str, JsonValue], grant_id: str, *, episode: int) -> None:
        async with self.allocation(str(state["allocation_id"])) as allocation:
            current = (
                await self.observe(allocation) if allocation.record and not allocation.record.get("deleted") else None
            )
            if current is None or current.status == "stopped" or current.runtime_id != state["runtime_id"]:
                # Runtime absence/replacement proves its input-serving process has ended.
                return
            await self.desktop(
                allocation,
                {
                    "protocol_version": 2,
                    "generation": current.state["viewer_generation"],
                    "operation": "revoke_browser",
                    "id": grant_id,
                    "episode": episode,
                },
            )

    async def upload_file(self, state: dict[str, JsonValue], name: str, content: bytes) -> str:
        if not name or name in {".", ".."} or any(c in name for c in ("/", "\\", "\x00")):
            raise BrowserProviderError("invalid_upload_name", certainty="not_dispatched")
        async with self.running(state) as (allocation, _):
            return await self.write_upload(allocation, secrets.token_hex(16), name, content)

    async def download_file(self, state: dict[str, JsonValue], guid: str, *, max_bytes: int) -> bytes:
        if not re.fullmatch(r"[a-fA-F0-9-]{36}", guid):
            raise BrowserProviderError("invalid_download", certainty="not_dispatched")
        async with self.running(state) as (allocation, _):
            return await self.read_download(allocation, guid, max_bytes)

    @abstractmethod
    def download_directory(self, allocation: Allocation) -> str: ...

    @abstractmethod
    async def write_upload(self, allocation: Allocation, identity: str, name: str, content: bytes) -> str: ...

    @abstractmethod
    async def read_download(self, allocation: Allocation, guid: str, max_bytes: int) -> bytes: ...

    @abstractmethod
    async def provision(self, allocation: Allocation) -> None: ...

    @abstractmethod
    async def launch(self, allocation: Allocation) -> LiveBrowser: ...

    @abstractmethod
    async def observe(self, allocation: Allocation) -> LiveBrowser | None: ...

    @abstractmethod
    async def halt(self, allocation: Allocation) -> None: ...

    @abstractmethod
    async def destroy(self, allocation: Allocation) -> None: ...

    @abstractmethod
    async def desktop(self, allocation: Allocation, request: dict) -> dict: ...

    async def close(self) -> None:
        """Runtime ownership survives the short-lived adapter."""
        return None


def desktop_result(response: dict) -> dict:
    if "error" in response:
        raise BrowserProviderError("viewer_operation_failed", certainty="known")
    return response["result"]
