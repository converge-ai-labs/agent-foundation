"""Linux debugging provider: private profiles and exact local process ownership."""

import asyncio
import json
import os
import shutil
import signal
import socket
import stat
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from a13n_service.providers.browser import runtime
from a13n_service.providers.browser.definition import BrowserProviderError, LiveBrowser
from a13n_service.providers.browser.selfhost import Allocation, SelfHosted, blocking, desktop_result
from a13n_service.providers.environment import desktop_guest

_CLEANUP_TIMEOUT = 30


class Configuration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Native(SelfHosted):
    def __init__(self, root: Path, chromium: str):
        super().__init__(root, "native")
        self.chromium = chromium

    async def create(self, allocation_id: str, recipe: BaseModel) -> LiveBrowser:
        binaries = [
            await blocking(shutil.which, binary)
            for binary in (self.chromium, "Xvfb", "openbox", "xauth", "x11vnc", "websockify")
        ]
        if sys.platform != "linux" or any(binary is None for binary in binaries):
            raise BrowserProviderError("native_dependencies_missing", certainty="not_dispatched")
        return await super().create(allocation_id, recipe)

    async def host(self, allocation: Allocation) -> str:
        machine = (await blocking(Path("/etc/machine-id").read_text)).strip()
        if allocation.record.get("host", machine) != machine:
            raise BrowserProviderError("native_host_changed", certainty="known")
        return machine

    async def provision(self, allocation: Allocation) -> None:
        allocation.record["host"] = await self.host(allocation)
        await blocking(desktop_guest.private_directory, allocation.root / "data")
        await allocation.save()

    async def information(self, allocation: Allocation) -> dict | None:
        await self.host(allocation)
        path = allocation.root / "data" / "runtime.json"
        return json.loads(await blocking(path.read_text)) if await blocking(path.exists) else None

    async def observe(self, allocation: Allocation) -> LiveBrowser | None:
        if not await blocking((allocation.root / "data").is_dir):
            return None
        info = await self.information(allocation)
        state = {"allocation_id": allocation.id, "runtime_id": info["runtime_id"] if info else None}
        boot = (await blocking(Path("/proc/sys/kernel/random/boot_id").read_text)).strip()
        if allocation.record.get("boot") != boot:
            return LiveBrowser(state, status="stopped", runtime_id=state["runtime_id"])
        if allocation.record.get("launch_unresolved"):
            raise BrowserProviderError("native_writer_unconfirmed")
        pid = allocation.record.get("pid")
        if pid is not None and (info is None or info["pid"] != pid):
            if await blocking(runtime.group_alive, pid):
                raise BrowserProviderError("native_start_pending")
        if info is None:
            return LiveBrowser(state, status="stopped", runtime_id=None)
        live = await blocking(runtime.process_identity, info["pid"]) == info["started"]
        if not live:
            if await blocking(runtime.group_alive, info["pid"]):
                raise BrowserProviderError("native_writer_unconfirmed")
            return LiveBrowser(state, status="stopped", runtime_id=info["runtime_id"])
        if not info["ready"]:
            raise BrowserProviderError("native_start_pending")
        state.update(
            cdp_url=f"ws://127.0.0.1:{info['cdp_port']}{info['cdp_path']}",
            viewer_url=f"ws://127.0.0.1:{info['viewer_port']}",
            viewer_generation=info["viewer_generation"],
        )
        return LiveBrowser(state, runtime_id=info["runtime_id"])

    async def launch(self, allocation: Allocation) -> LiveBrowser:
        info = await self.information(allocation)
        boot = (await blocking(Path("/proc/sys/kernel/random/boot_id").read_text)).strip()
        if allocation.record.get("boot") == boot:
            identities = {allocation.record.get("pid"), info["pid"] if info else None} - {None}
            for pid in identities:
                if await blocking(runtime.group_alive, pid):
                    raise BrowserProviderError("native_writer_unconfirmed", certainty="not_dispatched")
        # No other user can supply executable, flags or a profile path through a Browser recipe.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            viewer_port = reservation.getsockname()[1]
        recipe = allocation.record["recipe"]
        command = [
            sys.executable,
            "-I",
            str(Path(runtime.__file__)),
            "--root",
            str(allocation.root / "data"),
            "--guest",
            str(Path(desktop_guest.__file__)),
            "--chromium",
            self.chromium,
            "--viewer-port",
            str(viewer_port),
            "--width",
            str(recipe["width"]),
            "--height",
            str(recipe["height"]),
            "--parent",
            str(os.getpid()),
        ]
        allocation.record.update(boot=boot, launch_unresolved=True)
        await allocation.save()
        log = await blocking((allocation.root / "runtime.log").open, "ab")

        async def spawn_and_record():
            try:
                # PDEATHSIG tracks the spawning thread: use the event-loop thread.
                process = await asyncio.create_subprocess_exec(
                    *command, start_new_session=True, stdin=asyncio.subprocess.DEVNULL, stdout=log, stderr=log
                )
            except Exception:
                allocation.record.pop("launch_unresolved")
                await allocation.save()
                raise
            finally:
                await blocking(log.close)
            try:
                allocation.record["pid"] = process.pid
                allocation.record["started"] = await blocking(runtime.process_identity, process.pid)
                allocation.record.pop("launch_unresolved")
                await allocation.save()
            except Exception:
                # The durable unresolved marker fences the profile if cleanup cannot be confirmed.
                allocation.record["launch_unresolved"] = True
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                async with asyncio.timeout(_CLEANUP_TIMEOUT):
                    await process.wait()
                    while await blocking(runtime.group_alive, process.pid):
                        await asyncio.sleep(0.1)
                allocation.record.pop("launch_unresolved")
                await allocation.save()
                raise
            return process

        # Keep the allocation lock until the child identity is durable, even when
        # the caller uses raw Task.cancel rather than an AnyIO cancellation scope.
        spawn = asyncio.create_task(spawn_and_record())
        cancelled = None
        while not spawn.done():
            try:
                await asyncio.shield(spawn)
            except asyncio.CancelledError as error:
                cancelled = error
            except Exception:
                break
        try:
            process = spawn.result()
        except Exception as error:
            if cancelled is not None:
                raise cancelled from error
            raise
        if cancelled is not None:
            raise cancelled
        async with asyncio.timeout(40):
            while True:
                if process.returncode is not None:
                    raise BrowserProviderError("native_start_failed", certainty="known")
                info = await self.information(allocation)
                if info and info["pid"] == process.pid and info["ready"]:
                    result = await self.observe(allocation)
                    assert result is not None
                    return result
                await asyncio.sleep(0.1)

    async def halt(self, allocation: Allocation) -> None:
        await self.host(allocation)
        boot = (await blocking(Path("/proc/sys/kernel/random/boot_id").read_text)).strip()
        if allocation.record.get("boot") != boot:
            return
        if allocation.record.get("launch_unresolved"):
            raise BrowserProviderError("native_writer_unconfirmed")
        pid, started = allocation.record.get("pid"), allocation.record.get("started")
        if pid is None:
            return
        if await blocking(runtime.process_identity, pid) == started:
            try:
                await blocking(os.kill, pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        async with asyncio.timeout(30):
            while await blocking(runtime.group_alive, pid):
                await asyncio.sleep(0.1)

    async def destroy(self, allocation: Allocation) -> None:
        await self.halt(allocation)
        path = allocation.root / "data"
        if await blocking(path.exists):
            await blocking(shutil.rmtree, path)

    def download_directory(self, allocation: Allocation) -> str:
        return str(allocation.root / "data" / "downloads")

    async def write_upload(self, allocation: Allocation, identity: str, name: str, content: bytes) -> str:
        directory = allocation.root / "data" / "uploads" / identity
        await blocking(desktop_guest.private_directory, directory)
        path = directory / name
        await blocking(path.write_bytes, content)
        return str(path)

    async def read_download(self, allocation: Allocation, guid: str, max_bytes: int) -> bytes:
        def read() -> bytes:
            descriptor = os.open(Path(self.download_directory(allocation)) / guid, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(descriptor, "rb") as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise BrowserProviderError("download_size_or_kind", certainty="not_dispatched")
                if info.st_size > max_bytes:
                    raise BrowserProviderError("download_too_large", certainty="not_dispatched")
                content = file.read(max_bytes + 1)
                if len(content) > max_bytes:
                    raise BrowserProviderError("download_too_large", certainty="not_dispatched")
                return content

        return await blocking(read)

    async def desktop(self, allocation: Allocation, request: dict) -> dict:
        return desktop_result(await desktop_guest.call(allocation.root / "data" / "desktop", request))
