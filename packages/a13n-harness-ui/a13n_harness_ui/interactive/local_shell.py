"""User-owned host commands; never a model tool or a source of model input."""

from __future__ import annotations

import asyncio
import codecs
import os
import signal
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class LocalShellEvent:
    kind: Literal["started", "output", "finished"]
    command: str
    stream: Literal["stdout", "stderr"] | None = None
    text: str = ""
    phase: Literal["running", "exited", "cancelled", "timed_out", "failed"] = "running"
    exit_code: int | None = None
    elapsed: float = 0
    truncated: bool = False


def validate_local_shell_support() -> None:
    if os.name != "posix":
        raise ValueError(
            "!command currently requires a POSIX host with process-group cleanup. Model shell tools remain available."
        )


async def run_local_shell(
    command: str,
    directory: Path,
    emit: Callable[[LocalShellEvent], None],
    *,
    timeout: float = 120,
    output_limit: int = 256 * 1024,
) -> None:
    """Stream both pipes, bound retained output, and reap the owned process group."""
    validate_local_shell_support()
    started = time.monotonic()
    emit(LocalShellEvent("started", command))
    process: asyncio.subprocess.Process | None = None
    readers: list[asyncio.Task[None]] = []
    phase: Literal["exited", "cancelled", "timed_out", "failed"] = "failed"
    retained = 0
    truncated = False

    async def read(stream: asyncio.StreamReader, name: Literal["stdout", "stderr"]) -> None:
        nonlocal retained, truncated
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while True:
            data = await stream.read(8192)
            available = max(0, output_limit - retained)
            shown = data[:available]
            retained += len(shown)
            truncated |= len(shown) < len(data)
            text = decoder.decode(shown, final=not data)
            if text:
                emit(LocalShellEvent("output", command, stream=name, text=text))
            if not data:
                return

    async def cleanup() -> None:
        if process is None:
            return
        # Even an exited parent can leave descendants holding our pipes open.
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        await asyncio.gather(*readers, return_exceptions=True)

    spawn = asyncio.create_task(
        asyncio.create_subprocess_shell(
            command,
            cwd=directory,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    )
    try:
        try:
            process = await asyncio.shield(spawn)
        except asyncio.CancelledError:
            process = await spawn
            raise
        finally:
            # Install drainers even when cancellation won the spawn race.
            if process is not None:
                assert process.stdout is not None and process.stderr is not None
                readers = [
                    asyncio.create_task(read(process.stdout, "stdout")),
                    asyncio.create_task(read(process.stderr, "stderr")),
                ]
        assert process is not None
        async with asyncio.timeout(timeout):
            await process.wait()
            # Termination must never cancel the only readers of full pipes.
            await asyncio.shield(asyncio.gather(*readers))
        phase = "exited"
    except TimeoutError:
        phase = "timed_out"
    except asyncio.CancelledError:
        phase = "cancelled"
        raise
    finally:
        task = asyncio.create_task(cleanup())
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise
        finally:
            emit(
                LocalShellEvent(
                    "finished",
                    command,
                    phase=phase,
                    exit_code=process.returncode if process else None,
                    elapsed=time.monotonic() - started,
                    truncated=truncated,
                )
            )
