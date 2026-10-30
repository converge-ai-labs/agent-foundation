"""Default Run-local recovery from native model context overflow."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import cast

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.settings import ModelSettings

from a13n_harness._context_recovery import CONTEXT_RECOVERY_STATE_ID, ContextRecoveryState, prepare_recovery
from a13n_harness.capabilities.context import (
    CompactionCapability,
    ContextRestoredEvent,
    _replace_context_history,
    _requires_exact_boundary,
)
from a13n_harness.context import AgentContext
from a13n_harness.errors import DefinitionError
from a13n_harness.recovery import ContextRecoveryPolicy
from a13n_harness.usage import UsageCapability

CONTEXT_RECOVERY_CAPABILITY_ID = "a13n.context-recovery-runtime"


@dataclass(init=False)
class ContextRecoveryCapability(AbstractCapability[AgentContext]):
    """Builder-owned recovery; proactive compaction remains independently optional."""

    id = CONTEXT_RECOVERY_CAPABILITY_ID

    def __init__(self, policy: ContextRecoveryPolicy) -> None:
        self.policy = policy
        self._request: ModelRequestContext | None = None
        self._compaction_attempted = False

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost", wrapped_by=(CompactionCapability,), wraps=(UsageCapability,))

    async def for_run(self, ctx: RunContext[AgentContext]) -> AbstractCapability[AgentContext]:
        existing = ctx.deps._run_capability(CONTEXT_RECOVERY_CAPABILITY_ID)
        if existing is not None:
            if not isinstance(existing, ContextRecoveryCapability):
                raise DefinitionError(
                    "Context recovery has an incompatible run replacement.", code="capability_type_mismatch"
                )
            return existing
        replacement = ContextRecoveryCapability(self.policy)
        ctx.deps._record_run_capability(CONTEXT_RECOVERY_CAPABILITY_ID, replacement)
        return replacement

    async def before_model_request(
        self, ctx: RunContext[AgentContext], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        state = await ctx.deps.state.read(CONTEXT_RECOVERY_STATE_ID, ContextRecoveryState, version="1")
        if state is not None:
            # Remote history must remain detached even in later Runs or when recovery is disabled.
            settings = dict(request_context.model_settings or {})
            settings.update(
                openai_conversation_id=None,
                openai_previous_response_id=None,
                xai_previous_response_id=None,
                google_cached_content=None,
            )
            request_context = replace(request_context, model_settings=cast(ModelSettings, settings))
        if not ctx.deps._compacting and ctx.run_id == ctx.deps._model_recovery.attempt_id:
            self._request = request_context
        return request_context

    def record_primary_progress(self) -> None:
        """Allow normal compaction again after an accepted primary response."""
        self._compaction_attempted = False

    async def recover_overflow(self, ctx: RunContext[AgentContext]) -> None:
        """Prepare replacement before native collaborators leave the failed attempt."""
        if not self.policy.enabled or self._request is None or ctx.deps._compacting:
            return
        previous = await ctx.deps.state.read(CONTEXT_RECOVERY_STATE_ID, ContextRecoveryState, version="1")
        if previous is not None and previous.scope_id == (ctx.deps.context_recovery_scope_id or ctx.deps.run_id):
            return
        request = self._request
        if ctx.deps._tool_executions.history_calls or _requires_exact_boundary(ctx, request.messages):
            return
        if self._compaction_attempted:
            return
        self._compaction_attempted = True
        compaction = next(
            (capability for capability in ctx.capabilities.values() if isinstance(capability, CompactionCapability)),
            None,
        )
        # Reuse the normal implementation without installing proactive compaction.
        compaction = compaction if compaction is not None else CompactionCapability()
        outcome = await compaction.compact_overflow(ctx, request)
        if outcome != "overflow":
            if outcome == "compacted":
                ctx.deps._model_recovery.context_replacement = tuple(ctx.messages)
            return
        rebuilt = await prepare_recovery(
            ctx, request, policy=self.policy, compaction_policy=compaction.policy, background=compaction.background
        )
        replacement = _replace_context_history(ctx, request, list(rebuilt.messages), set())
        ctx.messages[:] = replacement.messages
        ctx.deps._model_recovery.context_recovery_pending = True
        ctx.deps._model_recovery.context_replacement = tuple(ctx.messages)
        await ctx.emit(ContextRestoredEvent(source="recovery", operation_id=f"recovery-{ctx.deps.run_id}"))
        from a13n_harness.tools.discovery import finish_discovery_replacement

        await finish_discovery_replacement(ctx)


def context_recovery_enabled(ctx: RunContext[AgentContext]) -> bool:
    capability = ctx.capabilities.get(CONTEXT_RECOVERY_CAPABILITY_ID)
    return isinstance(capability, ContextRecoveryCapability) and capability.policy.enabled
