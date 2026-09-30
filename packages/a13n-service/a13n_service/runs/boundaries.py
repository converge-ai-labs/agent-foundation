"""Safe boundaries of a Harness run, where the worker commits a checkpoint.

The capability exports the state at each boundary, with the memory cursors that history was delivered, and marks
the boundary's position in the event stream with a `SafeBoundary` event. The producer captures compact display with canonical history before export awaits. The consumer commits
that detached pair, even if native output advances before the marker is delivered, then acknowledges the boundary.

- Before a model request the hook does not wait: the marker only reaches the stream once the request starts,
  and a model call needs no durable checkpoint first. The request can always be sent again, so this is where
  an attempt yields on handoff.
- Before tool execution the hook waits for the acknowledgement, so a tool call is durable (in state and as an
  in-progress display item) before it can have any effect. Parallel calls of one response share one commit.

Hooks act only for the primary native run: compaction runs nested agents that pass through the same hooks.
"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from a13n_harness import AgentContext, HarnessState
from a13n_harness.model_context import ModelContextCoordinatorCapability
from a13n_stream_protocol import DisplayCapture, DisplaySnapshot
from pydantic_ai import RunContext
from pydantic_ai.capabilities import (
    AbstractCapability,
    CapabilityOrdering,
    ValidatedToolArgs,
    WrapModelRequestHandler,
    WrapRunHandler,
)
from pydantic_ai.messages import CapabilityEvent, ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition


@dataclass(kw_only=True)
class SafeBoundary(CapabilityEvent, namespace="a13n.service", name="boundary"):
    token: int = 0
    # "model" before a model request, "tool" before tool execution.
    at: Literal["model", "tool"] = "model"


@dataclass(frozen=True, slots=True)
class Staged:
    """What one boundary commits: the exported state and the memory cursors its history holds context as of."""

    state: HarnessState
    cursors: dict[str, str | None]
    display: DisplaySnapshot


class Boundaries(AbstractCapability[AgentContext]):
    id = "a13n.service.boundaries"

    def __init__(self, cursors: Callable[[], dict[str, str | None]], display: DisplayCapture) -> None:
        self.display = display
        # The run's delivered memory cursors, snapshotted with each exported state.
        self.cursors = cursors
        self.primary: str | None = None
        self.states: dict[int, Staged] = {}
        self.acknowledged: dict[int, asyncio.Future[None]] = {}
        self.tokens = 0
        # The history length of the latest staged boundary; a boundary without new history is not staged again.
        self.staged_length = -1

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(wrapped_by=(ModelContextCoordinatorCapability,))

    def take(self, token: int) -> Staged:
        return self.states.pop(token)

    def acknowledge(self, token: int) -> None:
        if (waiting := self.acknowledged.pop(token, None)) is not None and not waiting.done():
            waiting.set_result(None)

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: WrapRunHandler) -> Any:
        owner = self.primary is None
        if owner:
            self.primary = ctx.run_id
        try:
            return await handler()
        finally:
            if owner:
                self.primary = None

    async def wrap_model_request(
        self,
        ctx: RunContext[AgentContext],
        *,
        request_context: ModelRequestContext,
        handler: WrapModelRequestHandler,
    ) -> ModelResponse:
        if ctx.run_id == self.primary:
            await self._stage(ctx, ctx.messages, "model")
        return await handler(request_context)

    async def before_tool_execute(
        self, ctx: RunContext[AgentContext], *, call: ToolCallPart, tool_def: ToolDefinition, args: ValidatedToolArgs
    ) -> ValidatedToolArgs:
        if ctx.run_id == self.primary:
            token = await self._stage(ctx, ctx.messages, "tool")
            if token in self.acknowledged:
                await asyncio.shield(self.acknowledged[token])
        return args

    async def _stage(
        self, ctx: RunContext[AgentContext], messages: list[ModelMessage], at: Literal["model", "tool"]
    ) -> int:
        """Stage the state at this boundary once per history length and return its token."""
        if len(messages) == self.staged_length:
            return self.tokens
        # Registered before the first await, so a parallel call of the same response waits for this commit.
        self.staged_length, self.tokens = len(messages), self.tokens + 1
        token = self.tokens
        self.acknowledged[token] = asyncio.get_running_loop().create_future()
        cursors = self.cursors()  # Taken with `messages`, before the export awaits.
        display = self.display.capture(ctx.deps.run_id, messages)
        self.states[token] = Staged(await ctx.deps.export_state(messages), cursors, display)
        await ctx.emit(SafeBoundary(token=token, at=at))
        return token
