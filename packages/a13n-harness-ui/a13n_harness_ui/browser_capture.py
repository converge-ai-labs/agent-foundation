"""Read-only native window preview transport, independent of browser automation."""

from __future__ import annotations

import asyncio
import struct
import sys
from typing import Any, cast

from a13n_harness_ui.errors import HarnessUiError

MAX_FRAME = 8 * 1024 * 1024


def window_ids(pid: int) -> set[int]:
    # Window metadata does not capture pixels or request Screen Recording permission.
    import Quartz  # pyright: ignore[reportMissingImports]

    quartz = cast(Any, Quartz)  # Framework symbols are resolved dynamically by PyObjC.
    return {
        int(window["kCGWindowNumber"])
        for window in quartz.CGWindowListCopyWindowInfo(quartz.kCGWindowListOptionAll, 0)
        if window.get("kCGWindowOwnerPID") == pid
        and window.get("kCGWindowLayer") == 0
        and window["kCGWindowBounds"]["Width"] > 1
        and window["kCGWindowBounds"]["Height"] > 1
    }


class WindowPreview:
    """One capture worker and its bounded, latest-frame delivery queue."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.frames: asyncio.Queue[bytes | HarnessUiError] = asyncio.Queue(maxsize=1)
        self.closed = False
        self.shutdown: asyncio.Task[None] | None = None
        self.reader = asyncio.create_task(self.read_frames())

    @classmethod
    async def start(cls, pid: int, window: int) -> WindowPreview:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "a13n_harness_ui._browser_capture",
            str(pid),
            str(window),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        preview = cls(process)
        try:
            async with asyncio.timeout(15):
                frame = await preview.receive()
            preview.publish(frame)
            return preview
        except BaseException:
            await preview.wait_closed()
            raise

    def publish(self, value: bytes | HarnessUiError) -> None:
        if self.frames.full():
            self.frames.get_nowait()
        self.frames.put_nowait(value)

    async def read_frames(self) -> None:
        assert self.process.stdout is not None and self.process.stderr is not None
        try:
            while not self.closed:
                (length,) = struct.unpack("!I", await self.process.stdout.readexactly(4))
                if not 0 < length <= MAX_FRAME:
                    raise ValueError("Invalid preview frame size")
                self.publish(await self.process.stdout.readexactly(length))
        except (asyncio.IncompleteReadError, OSError, ValueError):
            pass
        finally:
            self.close()

    async def receive(self) -> bytes:
        if self.closed and self.frames.empty():
            raise HarnessUiError("Window preview closed.", code="browser_preview_unavailable")
        value = await self.frames.get()
        if isinstance(value, HarnessUiError):
            raise value
        return value

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.process.returncode is None:
            try:
                self.process.terminate()
            except ProcessLookupError:
                pass
        self.publish(
            HarnessUiError(
                "Window preview unavailable. Unlock the Mac, keep the browser window visible, "
                "and check Screen Recording permission, then reconnect. "
                "If the window closed, reset the browser. Automation can continue without preview.",
                code="browser_preview_unavailable",
            )
        )
        self.shutdown = asyncio.create_task(self.reap())

    def is_closing(self) -> bool:
        return self.closed

    async def reap(self) -> None:
        self.reader.cancel()
        await asyncio.gather(self.reader, return_exceptions=True)
        try:
            async with asyncio.timeout(3):
                await self.process.communicate()
        except TimeoutError:
            self.process.kill()
            await self.process.communicate()

    async def wait_closed(self) -> None:
        self.close()
        assert self.shutdown is not None
        await asyncio.shield(self.shutdown)
