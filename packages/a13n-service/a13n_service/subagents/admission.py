"""State-first child admission preparation for one live parent Attempt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncResumeRequest,
    SubagentDelegationPlan,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import (
    Run,
    Thread,
    new_run_id,
    new_thread_id,
)
from a13n_service.interactions.objects import RunStateStore, StoredRunState
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


@dataclass(frozen=True, slots=True)
class ParentRunSource:
    """One detached parent observation, rechecked under the Attempt fence at commit."""

    authority: AttemptContext
    run: Run
    thread: Thread
    state: StoredRunState

    def require_authority(self, authority: AttemptContext) -> None:
        if (
            self.authority != authority
            or self.run.organization_id != authority.organization_id
            or self.run.id != authority.run_id
            or self.run.thread_id != authority.thread_id
            or self.thread.id != self.run.thread_id
            or self.thread.organization_id != self.run.organization_id
            or self.thread.session_id != self.run.session_id
            or self.state.envelope.run_id != self.run.id
            or self.state.envelope.thread_id != self.run.thread_id
        ):
            raise SubagentOperatorError("subagent_parent_context_mismatch", "Prepared parent source changed scope")


@dataclass(frozen=True, slots=True)
class PreparedChildAdmission[Candidate: (PreparedChildRunAcceptance, PreparedChildRunResume)]:
    candidate: Candidate
    parent: ParentRunSource
    source_state: StoredRunState | None = None


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
    ) -> PreparedChildAdmission[PreparedChildRunAcceptance]:
        if request.subagent_name != plan.child.declaration.name:
            raise SubagentOperatorError(
                "subagent_plan_invalid",
                "Delegation request does not match the frozen child plan",
            )
        observed = await self._parent(authority)
        parent, parent_state = observed.run, observed.state.envelope
        edge = require_frozen_subagent_edge(parent, parent_state, request.subagent_name)
        child = parent_state.effective_agent_config.child_configs[edge.child_agent_revision_id]
        definition_id = f"agent-config-{child.revision_content_digest[:24]}"
        if definition_id != plan.child.definition.definition_id:
            raise SubagentOperatorError(
                "subagent_definition_conflict", "Harness child differs from its accepted snapshot"
            )
        prepared = prepare_child_run(
            parent_run=parent,
            parent_state=parent_state,
            parent_run_attempt_id=authority.run_attempt_id,
            parent_run_attempt_fence=authority.attempt_number,
            parent_agent_instance_id=plan.parent.parent_agent_instance_id,
            subagent_name=request.subagent_name,
            delegated_input=delegated_input,
            child_definition_id=definition_id,
            child_agent_id=edge.child_agent_id,
            child_agent_revision_id=edge.child_agent_revision_id,
            child_effective_config=child.effective_config,
            connection_selections=child.connection_selections,
            child_thread_id=self._thread_id_factory(),
            child_run_id=self._run_id_factory(),
            relationship_id=self._relationship_id_factory(),
            execution_budget=parent.execution_budget,
            created_at=assume_utc(self._clock()),
            usage_limits=plan.usage_limits,
        )
        return PreparedChildAdmission(prepared, observed)

    async def prepare_resume(
        self,
        authority: AttemptContext,
        source: RetainedChildExecution,
        plan: SubagentDelegationPlan,
        request: AsyncResumeRequest,
        delegated_input: str,
    ) -> PreparedChildAdmission[PreparedChildRunResume]:
        if request.execution_id != source.relationship.id:
            raise SubagentOperatorError(
                "subagent_plan_invalid",
                "Resume request does not match its retained child source",
            )
        observed = await self._parent(authority)
        parent, parent_state = observed.run, observed.state.envelope
        source_state = await self._states.read_run(source.run)
        prepared = prepare_child_resume(
            parent_run=parent,
            parent_state=parent_state,
            parent_run_attempt_id=authority.run_attempt_id,
            parent_run_attempt_fence=authority.attempt_number,
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
        return PreparedChildAdmission(prepared, observed, source_state)

    async def _parent(self, authority: AttemptContext) -> ParentRunSource:
        async with short_session(self._sessions) as database:
            parent, _, thread = await read_attempt_authority(database, authority, assume_utc(self._clock()))
            parent_resource = parent.to_resource()
            thread_resource = thread.to_resource()
        stored = await self._states.read_run(parent_resource)
        return ParentRunSource(authority, parent_resource, thread_resource, stored)


__all__ = [
    "ChildRunAdmissionPreparer",
]
