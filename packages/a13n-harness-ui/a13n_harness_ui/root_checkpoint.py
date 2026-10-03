"""Stage canonical state in the producer; select checkpoints in the Host consumer."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from a13n_harness import HarnessEvent, HarnessState, HarnessStreamEvent
from a13n_harness.context import AgentContext
from a13n_harness.model_context import ModelContextCoordinatorCapability
from anyio import CancelScope
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import CapabilityEvent
from pydantic_ai.models import ModelRequestContext


@dataclass(kw_only=True)
class ThreadCheckpointEvent(CapabilityEvent, namespace="a13n.harness_ui", name="checkpoint"):
    continuation_id: str


@dataclass(kw_only=True)
class CheckpointBoundary(CapabilityEvent, namespace="a13n.harness_ui", name="checkpoint_boundary"):
    token: int


class RootCheckpointCapability(AbstractCapability[AgentContext]):
    """The consumer commits a staged state only after consuming its display prefix.

    A model-request hook must not wait for the consumer: Pydantic drains the
    marker when the request starts. Starting a model call grants no tool effect
    or persistence authority. The Host replaces the private marker with the
    selected continuation hint before publishing later model output.
    """

    id = "a13n.harness-ui.root-checkpoint"

    def __init__(self, save: Callable[[HarnessState], Awaitable[str]]) -> None:
        self._save = save
        self._active_run_id: str | None = None
        self._token = 0
        self._states: dict[int, HarnessState] = {}

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wrapped_by=(ModelContextCoordinatorCapability,))

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Callable[[], Awaitable[Any]]) -> Any:
        # Nested compaction/helper runs cannot stage a root checkpoint.
        if self._active_run_id is not None:
            return await handler()
        self._active_run_id = ctx.run_id
        try:
            return await handler()
        finally:
            self._active_run_id = None

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        if self._active_run_id is not None and ctx.run_id == self._active_run_id:
            # Capture every request boundary, including same-length context
            # replacement and native recovery. History length is not identity.
            state = await ctx.deps.export_state(ctx.messages)
            self._token += 1
            self._states[self._token] = state
            await ctx.emit(CheckpointBoundary(token=self._token))
        return request_context

    async def consume(self, item: HarnessStreamEvent[Any]) -> HarnessStreamEvent[Any]:
        """Join selection before continuing the one Host consumer, also on cancellation."""
        if not isinstance(item, HarnessEvent) or not isinstance(item.event, CheckpointBoundary):
            return item
        state = self._states.pop(item.event.token)
        save = asyncio.ensure_future(self._save(state))
        with CancelScope(shield=True):
            cancelled: asyncio.CancelledError | None = None
            while not save.done():
                try:
                    await asyncio.shield(save)
                except asyncio.CancelledError as exc:
                    cancelled = exc
            save.result()
            if cancelled is not None:
                raise cancelled
        return replace(item, event=ThreadCheckpointEvent(continuation_id=save.result()))
