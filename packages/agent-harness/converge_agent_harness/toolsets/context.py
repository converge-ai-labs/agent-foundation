"""Context-management Toolsets with concrete handoff state transitions."""

from __future__ import annotations

import asyncio
from secrets import token_urlsafe
from typing import TYPE_CHECKING, Annotated

from pydantic import Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.exceptions import ToolFailed
from pydantic_ai.toolsets import FunctionToolset

from converge_agent_harness.context import AgentContext
from converge_agent_harness.errors import DefinitionError
from converge_agent_harness.events import (
    ContextOperationFailedPayload,
    ContextOperationPreparedPayload,
    ContextOperationStartedPayload,
    emit_harness_event,
)

if TYPE_CHECKING:
    from converge_agent_harness.capabilities.context import _HandoffState


class HandoffToolset:
    """Own the model-facing summarize transition for one run-local handoff state."""

    def __init__(
        self,
        *,
        owner: AbstractCapability[AgentContext],
        context: AgentContext,
        state: _HandoffState,
    ) -> None:
        self._owner = owner
        self._context = context
        self._state = state.model_copy(deep=True)
        self._state_lock = asyncio.Lock()

    @property
    def state(self) -> _HandoffState:
        return self._state.model_copy(deep=True)

    def get_toolset(self) -> FunctionToolset[AgentContext]:
        return FunctionToolset(tools=[self.summarize], id="converge-handoff-tools")

    async def replace_state(self, state: _HandoffState) -> None:
        from converge_agent_harness.capabilities.context import (
            _CONTEXT_STATE_VERSION,
            HANDOFF_CAPABILITY_ID,
        )

        state = state.model_copy(deep=True)
        await self._context.state.write(
            HANDOFF_CAPABILITY_ID,
            state,
            version=_CONTEXT_STATE_VERSION,
        )
        self._state = state

    async def summarize(
        self,
        ctx: RunContext[AgentContext],
        content: Annotated[
            str,
            Field(
                min_length=1,
                description=(
                    "Continuation summary preserving intent, state, decisions, past interactions, and next step."
                ),
            ),
        ],
        files_to_inspect: Annotated[
            list[str] | None,
            Field(
                default=None,
                description="Logical file paths to remind the resumed agent to inspect; no contents are loaded.",
            ),
        ] = None,
    ) -> str:
        from converge_agent_harness.capabilities.context import (
            _HandoffState,
            _render_summary,
            _safe_context_error_code,
        )

        self._require_context(ctx)
        async with self._state_lock:
            if self._state.summary is not None:
                raise ToolFailed("A context summary is already prepared for the next model boundary.")
            rendered = _render_summary(content)
            operation_id = self._state.operation_id or f"handoff-{token_urlsafe(9)}"
            state = _HandoffState(
                operation_id=operation_id,
                summary=rendered,
                files=tuple(files_to_inspect or ()),
            )
            if self._state.operation_id is None:
                started = state.model_copy(update={"summary": None, "files": ()})
                await self.replace_state(started)
                await emit_harness_event(
                    ctx.deps.events,
                    kind="context",
                    payload=ContextOperationStartedPayload(
                        type="handoff_started",
                        operation_id=operation_id,
                    ),
                )
            try:
                await self.replace_state(state)
            except BaseException as exc:
                error_code = _safe_context_error_code(exc)
                await emit_harness_event(
                    ctx.deps.events,
                    kind="context",
                    payload=ContextOperationFailedPayload(
                        type="handoff_failed",
                        operation_id=operation_id,
                        failed_phase="summary_persistence",
                        error_code=error_code,
                        retryable=True,
                    ),
                )
                raise
            await emit_harness_event(
                ctx.deps.events,
                kind="context",
                payload=ContextOperationPreparedPayload(
                    type="handoff_prepared",
                    operation_id=operation_id,
                    summary_size=len(rendered.encode("utf-8")),
                    files_count=len(state.files),
                ),
            )
            return "Summary accepted. The next model boundary will continue from restored context."

    def _require_context(self, ctx: RunContext[AgentContext]) -> None:
        from converge_agent_harness.capabilities.context import HANDOFF_CAPABILITY_ID

        if ctx.deps is not self._context or ctx.capabilities.get(HANDOFF_CAPABILITY_ID) is not self._owner:
            raise DefinitionError(
                "Handoff Toolset cannot cross logical runs.",
                code="capability_scope_invalid",
            )


__all__ = ["HandoffToolset"]
