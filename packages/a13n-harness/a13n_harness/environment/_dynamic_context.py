"""Run-local model notifications for Environment changes."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import RunContext

from a13n_harness.context import AgentContext
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.model_context import (
    ModelContextNext,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
)


class _DynamicEnvironmentContext:
    """Coalesce Environment change notices while a native run is active."""

    def __init__(self) -> None:
        self._notice_pending = False
        self._active_context: RunContext[AgentContext] | None = None
        self._observer_task: asyncio.Task[None] | None = None

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Any) -> Any:
        self._active_context = ctx
        self._ensure_observer(ctx)
        try:
            return await handler()
        finally:
            if self._active_context is ctx:
                self._active_context = None

    async def wrap_model_context(
        self,
        ctx: RunContext[AgentContext],
        request: ModelContextProjectionRequest,
        handler: ModelContextNext,
    ) -> ModelContextProjection:
        projection = await handler(request)
        if request.kind is ModelContextRequestKind.INPUT:
            self._notice_pending = False
        return projection

    def _ensure_observer(self, ctx: RunContext[AgentContext]) -> None:
        if self._observer_task is not None:
            return
        self._observer_task = asyncio.create_task(self._observe_changes(ctx.deps))
        self._observer_task.add_done_callback(_consume_task_result)

    async def _observe_changes(self, context: AgentContext) -> None:
        cursor = 0
        while True:
            try:
                changes = await context.environment._read_changes(after_sequence=cursor, wait=True)
            except EnvironmentError as exc:
                if exc.code == "environment_closed":
                    return
                raise
            if not changes:
                return
            cursor = changes[-1].sequence
            active = self._active_context
            if active is not None and not self._notice_pending:
                self._notice_pending = True
                active.enqueue(
                    "The Environment mounts changed. A fresh bounded mount snapshot is attached to this request.",
                    priority="asap",
                )


def _consume_task_result(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        # The Environment lifecycle remains authoritative; notices are best effort.
        return


__all__ = ["_DynamicEnvironmentContext"]
