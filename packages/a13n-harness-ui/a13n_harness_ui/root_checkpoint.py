"""Host-owned continuation checkpoints at complete root model-request boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from a13n_harness import HarnessState
from a13n_harness.context import AgentContext
from a13n_harness.model_context import ModelContextCoordinatorCapability
from a13n_stream_protocol.display import DisplaySnapshot
from a13n_stream_protocol.session import DisplayCapture
from anyio import CancelScope
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering, WrapModelRequestHandler
from pydantic_ai.messages import CapabilityEvent, ModelResponse
from pydantic_ai.models import ModelRequestContext


@dataclass(kw_only=True)
class ThreadCheckpointEvent(CapabilityEvent, namespace="a13n.harness_ui", name="checkpoint"):
    continuation_id: str
    display_sequence: int = 0


class RootCheckpointCapability(AbstractCapability[AgentContext]):
    """Persist complete messages and state, never partial response deltas or helper-model histories."""

    id = "a13n.harness-ui.root-checkpoint"

    def __init__(
        self,
        save: Callable[[HarnessState, DisplaySnapshot | None], Awaitable[str]],
        *,
        display: DisplayCapture | None = None,
    ) -> None:
        self._save = save
        self._display = display
        self._active_run_id: str | None = None

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(wrapped_by=(ModelContextCoordinatorCapability,))

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        # Compaction can reenter the same Agent with the same AgentContext but a
        # different Pydantic Run. Only the outer execution owns the Thread head.
        if self._active_run_id is not None:
            return await handler()
        self._active_run_id = ctx.run_id
        try:
            return await handler()
        finally:
            self._active_run_id = None

    async def wrap_model_request(
        self, ctx: RunContext[AgentContext], *, request_context: ModelRequestContext, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        if self._active_run_id is not None and ctx.run_id == self._active_run_id:
            # Before-hooks and the context coordinator have committed their state
            # and history transformations. Provider-facing messages can be merged
            # or filtered; checkpoint only the canonical native history.
            snapshot = self._display.capture(ctx.deps.run_id, ctx.messages) if self._display is not None else None
            state = await ctx.deps.export_state(ctx.messages)
            # Native model cancellation uses Task.cancel(), which an AnyIO
            # shield alone cannot defer. Join publication, head advancement and
            # its stream marker before cancellation can start terminal saving.
            with CancelScope(shield=True):
                checkpoint = asyncio.create_task(self._checkpoint(ctx, state, snapshot))
                cancelled: asyncio.CancelledError | None = None
                while not checkpoint.done():
                    try:
                        await asyncio.shield(checkpoint)
                    except asyncio.CancelledError as exc:
                        cancelled = exc
                checkpoint.result()
                if cancelled is not None:
                    raise cancelled
        return await handler(request_context)

    async def _checkpoint(
        self, ctx: RunContext[AgentContext], state: HarnessState, snapshot: DisplaySnapshot | None
    ) -> None:
        continuation_id = await self._save(state, snapshot)
        await ctx.emit(
            ThreadCheckpointEvent(
                continuation_id=continuation_id,
                display_sequence=snapshot.position.sequence if snapshot is not None else 0,
            )
        )
