"""One process-local, explicitly requested browser installation on the Host."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable

from a13n_harness_ui.browser_setup import BrowserInstallState, BrowserSetup, inspect_browser


class BrowserInstaller:
    def __init__(self) -> None:
        self.state = BrowserInstallState()
        self.preparation: BrowserSetup | None = None
        self.task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, preparation: BrowserSetup, authorize: Callable[[], Awaitable[None]]) -> None:
        self.preparation = preparation
        self.state = BrowserInstallState(status="installing")
        self.task = asyncio.create_task(self.install(preparation, authorize))

    def output(self, text: str) -> None:
        self.state = self.state.model_copy(update={"output": (self.state.output + text)[-16384:]})

    async def install(self, preparation: BrowserSetup, authorize: Callable[[], Awaitable[None]]) -> None:
        try:
            for step in preparation.steps:
                await authorize()
                self.state = self.state.model_copy(update={"step": step.title})
                self.output(f"\n{step.title}\n")
                await self.run_step(step.command)
            await authorize()
            verification = await inspect_browser()
            if not verification.ready:
                missing = ", ".join(item.name for item in verification.requirements if not item.installed)
                raise RuntimeError(
                    f"Installation finished but the Host is not ready. Missing: {missing or verification.message}"
                )
            self.state = self.state.model_copy(update={"status": "succeeded", "step": "Installation finished"})
        except asyncio.CancelledError:
            self.state = self.state.model_copy(
                update={
                    "status": "failed",
                    "error": "Installation interrupted by server shutdown. Check again before retrying.",
                }
            )
            raise
        except TimeoutError:
            self.state = self.state.model_copy(
                update={
                    "status": "failed",
                    "error": "Installation timed out after 20 minutes. Check the output before retrying.",
                }
            )
        except Exception as exc:
            self.state = self.state.model_copy(update={"status": "failed", "error": str(exc)})

    async def run_step(self, command: str) -> None:
        # The plan comes exclusively from Host inspection, never request input.
        # No sudo, interactive stdin, shell profile or credential prompt.
        process = await asyncio.create_subprocess_exec(
            "/bin/sh",
            "-eu",
            "-c",
            command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "DEBIAN_FRONTEND": "noninteractive", "PIP_NO_INPUT": "1", "UV_NO_PROGRESS": "1"},
        )
        completed = False
        try:
            async with asyncio.timeout(1200):
                assert process.stdout is not None
                while data := await process.stdout.read(4096):
                    self.output(data.decode("utf-8", errors="replace"))
                code = await process.wait()
                completed = True
                if code:
                    if code in {-signal.SIGKILL, 128 + signal.SIGKILL}:
                        raise RuntimeError(
                            f"{self.state.step} was killed (exit {code}). Check available Host memory and system logs before retrying."
                        )
                    raise RuntimeError(
                        f"{self.state.step} failed (exit {code}). Review the installation output and retry."
                    )
        finally:
            if not completed:
                # Also collect children that outlive an interrupted package manager.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    await asyncio.wait_for(process.wait(), 5)
                except TimeoutError:
                    pass
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    await process.wait()

    async def close(self) -> None:
        if self.running and self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
