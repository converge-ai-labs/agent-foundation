"""Serial Host checkpoint control, independent of public event delivery.

Each native boundary freezes state, delivered memory cursors and display together.
Model requests overlap checkpoint I/O; tools wait for commit and steering before effects.
Parallel tools share one completion. Only the primary native run stages checkpoints.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal

from a13n_harness import AgentContext, HarnessState
from a13n_harness.model_context import ModelContextCoordinatorCapability
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, ValidatedToolArgs, WrapRunHandler
from pydantic_ai.messages import ModelMessage, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition

from a13n_service.runs.display import Snapshot, open_tool_calls


@dataclass(frozen=True, slots=True)
class Staged:
    """One immutable checkpoint cut and its pre-effect completion signal."""

    at: Literal["model", "tool"]
    state: HarnessState
    cursors: dict[str, str | None]
    display: Snapshot
    completed: asyncio.Future[None]


class Boundaries(AbstractCapability[AgentContext]):
    id = "a13n.service.boundaries"

    def __init__(self, cursors: Callable[[], dict[str, str | None]]) -> None:
        self.cursors = cursors
        self.freeze_display: Callable[[frozenset[str]], Snapshot] | None = None
        self.primary: str | None = None
        self.accepting_steers = False
        self._queue: asyncio.Queue[Staged | None] = asyncio.Queue(maxsize=1)
        self._writer: asyncio.Task[None] | None = None
        self._completed: asyncio.Future[None] | None = None
        self._closed = False
        self.staged_length = -1

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wrapped_by=(ModelContextCoordinatorCapability,))

    @asynccontextmanager
    async def process(self, commit: Callable[[Staged], Awaitable[None]]) -> AsyncIterator[None]:
        """Own the serial writer; failure cancels execution and wakes tool waiters."""
        try:
            async with asyncio.TaskGroup() as tasks:
                self._writer = tasks.create_task(self._process(commit))
                yield
                await self._queue.put(None)
        except ExceptionGroup as error:
            # A single writer/execution failure keeps its Service error or lease semantics.
            if len(error.exceptions) == 1:
                raise error.exceptions[0] from error
            raise
        finally:
            self._closed = True
            if self._completed is not None and not self._completed.done():
                self._completed.cancel()
            while not self._queue.empty():
                pending = self._queue.get_nowait()
                if pending is not None and not pending.completed.done():
                    pending.completed.cancel()
                self._queue.task_done()

    async def _process(self, commit: Callable[[Staged], Awaitable[None]]) -> None:
        while True:
            staged = await self._queue.get()
            try:
                if staged is None:
                    return
                try:
                    if self._closed:
                        continue
                    await commit(staged)
                    if not staged.completed.done():
                        staged.completed.set_result(None)
                finally:
                    if not staged.completed.done():
                        staged.completed.cancel()
            finally:
                self._queue.task_done()

    def stop(self) -> None:
        """Keep the committed handoff cut; discard subsequent staged checkpoints."""
        self._closed = True

    async def drain(self) -> None:
        """Finish checkpoint and steering work before native scope teardown."""
        await self._queue.join()

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: WrapRunHandler) -> Any:
        owner = self.primary is None
        if owner:
            self.primary = ctx.run_id
            self.accepting_steers = True
        try:
            try:
                result = await handler()
            except Exception:
                if owner:
                    self.accepting_steers = False
                    await self.drain()
                raise
            if owner:
                self.accepting_steers = False
                # Steering can read attachments through the still-entered Environment.
                await self.drain()
            return result
        except asyncio.CancelledError:
            if owner and self._writer is not None:
                # Public iteration tears down the native scope before leaving process().
                # Stop I/O here rather than waiting on a writer cancelled only by that exit.
                self._writer.cancel()
                await asyncio.gather(self._writer, return_exceptions=True)
            raise
        finally:
            if owner:
                self.accepting_steers = False
                self.primary = None

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if ctx.run_id == self.primary:
            await self._stage(ctx, ctx.messages, "model")
        return request_context

    async def before_tool_execute(
        self, ctx: RunContext[AgentContext], *, call: ToolCallPart, tool_def: ToolDefinition, args: ValidatedToolArgs
    ) -> ValidatedToolArgs:
        if ctx.run_id == self.primary:
            completed = await self._stage(ctx, ctx.messages, "tool")
            await asyncio.shield(completed)
        return args

    async def _stage(
        self, ctx: RunContext[AgentContext], messages: list[ModelMessage], at: Literal["model", "tool"]
    ) -> asyncio.Future[None]:
        if self._closed:
            raise asyncio.CancelledError
        if len(messages) == self.staged_length:
            assert self._completed is not None
            return self._completed
        # Reserve before the first await: parallel tools wait for the same cut.
        self.staged_length = len(messages)
        completed = asyncio.get_running_loop().create_future()
        self._completed = completed
        try:
            cursors, open_calls = self.cursors(), open_tool_calls(messages)
            state = await ctx.deps.export_state(messages)
            assert self.freeze_display is not None
            display = self.freeze_display(open_calls)
            await self._queue.put(Staged(at, state, cursors, display, completed))
        except BaseException:
            completed.cancel()
            raise
        return completed
