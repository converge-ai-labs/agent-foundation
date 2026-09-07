"""State-first child admission preparation for one live parent Attempt."""

from __future__ import annotations

from collections.abc import Callable

from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncResumeRequest,
    SubagentDelegationPlan,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import (
    Run,
    new_run_id,
    new_thread_id,
)
from a13n_service.interactions.objects import RunStateStore
from a13n_service.interactions.state import RunStateEnvelope
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import new_child_run_relationship_id
from .execution_store import RetainedChildExecution, SubagentOperatorError
from .preparation import (
    PreparedChildRunAcceptance,
    PreparedChildRunResume,
    prepare_child_resume,
    prepare_child_run,
    require_frozen_subagent_edge,
)


class ChildRunAdmissionPreparer:
    """Prepare new children from accepted snapshots and resumes from their retained source."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        *,
        thread_id_factory: Callable[[], str] = new_thread_id,
        run_id_factory: Callable[[], str] = new_run_id,
        relationship_id_factory: Callable[[], str] = new_child_run_relationship_id,
        clock: Clock = utc_now,
    ) -> None:
        self._sessions = sessions
        self._states = states
        self._thread_id_factory = thread_id_factory
        self._run_id_factory = run_id_factory
        self._relationship_id_factory = relationship_id_factory
        self._clock = clock

    async def prepare_delegate(
        self,
        authority: AttemptContext,
        plan: SubagentDelegationPlan,
        request: AsyncDelegateRequest,
        delegated_input: str,
    ) -> PreparedChildRunAcceptance:
        if request.subagent_name != plan.child.declaration.name:
            raise SubagentOperatorError(
                "subagent_plan_invalid",
                "Delegation request does not match the frozen child plan",
            )
        parent, parent_state = await self._parent(authority)
        edge = require_frozen_subagent_edge(parent, parent_state, request.subagent_name)
        child = parent_state.effective_agent_config.child_configs[edge.child_agent_revision_id]
        definition_id = f"agent-config-{child.revision_content_digest[:24]}"
        if definition_id != plan.child.definition.definition_id:
            raise SubagentOperatorError(
                "subagent_definition_conflict", "Harness child differs from its accepted snapshot"
            )
        return prepare_child_run(
            parent_run=parent,
            parent_state=parent_state,
            parent_run_attempt_id=authority.run_attempt_id,
            parent_run_attempt_fence=authority.fence,
            parent_agent_instance_id=plan.parent.parent_agent_instance_id,
            subagent_name=request.subagent_name,
            delegated_input=delegated_input,
            child_definition_id=definition_id,
            child_agent_id=edge.child_agent_id,
            child_agent_revision_id=edge.child_agent_revision_id,
            child_effective_config=child.effective_config,
            connector_connection_selections=child.connector_connection_selections,
            mcp_connection_selections=child.mcp_connection_selections,
            child_thread_id=self._thread_id_factory(),
            child_run_id=self._run_id_factory(),
            relationship_id=self._relationship_id_factory(),
            recovery_budget=parent.recovery_budget,
            created_at=assume_utc(self._clock()),
            usage_limits=plan.usage_limits,
        )

    async def prepare_resume(
        self,
        authority: AttemptContext,
        source: RetainedChildExecution,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
        delegated_input: str,
    ) -> PreparedChildRunResume:
        if request.execution_id != source.relationship.id:
            raise SubagentOperatorError(
                "subagent_plan_invalid",
                "Resume request does not match its retained child source",
            )
        parent, parent_state = await self._parent(authority)
        source_state = await self._states.read_run(source.run)
        return prepare_child_resume(
            parent_run=parent,
            parent_state=parent_state,
            parent_run_attempt_id=authority.run_attempt_id,
            parent_run_attempt_fence=authority.fence,
            parent_agent_instance_id=plan.parent.parent_agent_instance_id,
            subagent_name=source.relationship.subagent_name,
            delegated_input=delegated_input,
            child_definition_id=source.child_definition_id,
            source_relationship=source.relationship,
            source_parent_run=source.parent_run,
            source_thread=source.thread,
            source_run=source.run,
            source_state=source_state.envelope,
            child_run_id=self._run_id_factory(),
            relationship_id=self._relationship_id_factory(),
            created_at=assume_utc(self._clock()),
            usage_limits=plan.usage_limits,
        )

    async def _parent(self, authority: AttemptContext) -> tuple[Run, RunStateEnvelope]:
        async with short_session(self._sessions) as database:
            parent, _, _ = await read_attempt_authority(database, authority, assume_utc(self._clock()))
            parent_resource = parent.to_resource()
        stored = await self._states.read_run(parent_resource)
        return parent_resource, stored.envelope


__all__ = [
    "ChildRunAdmissionPreparer",
]
