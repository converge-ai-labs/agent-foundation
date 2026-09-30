"""Coalesced Environment notices at native model-node boundaries."""

from __future__ import annotations

from typing import Any

from pydantic_ai import RunContext, TextContent

from a13n_harness.context import AgentContext


class _DynamicEnvironmentContext:
    """Observe committed changes synchronously before native input draining."""

    def __init__(self) -> None:
        self._active_run_id: str | None = None
        self._observed_sequence = 0

    async def wrap_run(self, ctx: RunContext[AgentContext], *, handler: Any) -> Any:
        # Same-Agent compaction borrows this capability but does not consume the
        # outer run's pending Environment notice or replace its active identity.
        if self._active_run_id is not None:
            return await handler()
        self._active_run_id = ctx.run_id
        try:
            return await handler()
        finally:
            self._active_run_id = None

    def before_model_node(self, ctx: RunContext[AgentContext]) -> None:
        if ctx.run_id != self._active_run_id:
            return
        sequence = ctx.deps.environment._change_sequence
        if sequence <= self._observed_sequence:
            return
        ctx.enqueue(
            TextContent(
                "The Environment mounts changed. A fresh bounded mount snapshot is attached to this request.",
                metadata={"display": False, "source_id": "a13n.environment"},
            ),
            priority="asap",
        )
        self._observed_sequence = sequence


__all__ = ["_DynamicEnvironmentContext"]
