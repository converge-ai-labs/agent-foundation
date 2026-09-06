"""State-first child admission preparation for one live parent Attempt."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from a13n_harness.capabilities import (
    AsyncDelegateRequest,
    AsyncResumeRequest,
    SubagentDelegationPlan,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents import EffectiveAgentConfig
from a13n_service.connectivity.selection_domain import (
    ConnectorConnectionRunSelection,
    MCPConnectionRunSelection,
)
from a13n_service.interactions.attempts import AttemptContext, read_attempt_authority
from a13n_service.interactions.domain import (
    RecoveryBudget,
    Run,
    new_run_id,
    new_thread_id,
)
from a13n_service.interactions.objects import RunStateStore
from a13n_service.interactions.state import RunStateEnvelope
from a13n_service.storage import short_session
from a13n_service.temporal import Clock, assume_utc, utc_now

from .domain import (
    ChildCancellationPolicy,
    ChildResultVisibility,
    new_child_run_relationship_id,
)
from .execution_store import RetainedChildExecution, SubagentOperatorError
from .preparation import (
    PreparedChildRunAcceptance,
    PreparedChildRunResume,
    prepare_child_resume,
    prepare_child_run,
)


@dataclass(frozen=True, slots=True)
class ChildRunAdmissionProfile:
    """Exact child runtime facts frozen before the parent enters Harness."""

    agent_id: str
    agent_revision_id: str
    definition_id: str
    effective_config: EffectiveAgentConfig
    connector_connection_selections: tuple[ConnectorConnectionRunSelection, ...]
    mcp_connection_selections: tuple[MCPConnectionRunSelection, ...]
    recovery_budget: RecoveryBudget
    cancellation_policy: ChildCancellationPolicy = ChildCancellationPolicy.independent
    result_visibility: ChildResultVisibility = ChildResultVisibility.parent_thread


class ProfileChildRunAdmissionPreparer:
    """Build immutable child candidates from an Attempt-scoped frozen catalog."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        profiles: Mapping[str, ChildRunAdmissionProfile],
        *,
        thread_id_factory: Callable[[], str] = new_thread_id,
        run_id_factory: Callable[[], str] = new_run_id,
        relationship_id_factory: Callable[[], str] = new_child_run_relationship_id,
        clock: Clock = utc_now,
    ) -> None:
        if not profiles:
            raise ValueError("child admission profile catalog must not be empty")
        if any(not name or name != name.strip() for name in profiles):
            raise ValueError("child admission profile names must be non-blank and normalized")
        self._sessions = sessions
        self._states = states
        self._profiles = dict(profiles)
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
        profile = self._profile(plan)
        parent, parent_state = await self._parent(authority)
        return prepare_child_run(
            parent_run=parent,
            parent_state=parent_state,
            parent_run_attempt_id=authority.run_attempt_id,
            parent_run_attempt_generation=authority.fence,
            parent_agent_instance_id=plan.parent.parent_agent_instance_id,
            subagent_name=request.subagent_name,
            delegated_input=delegated_input,
            child_definition_id=profile.definition_id,
            child_agent_id=profile.agent_id,
            child_agent_revision_id=profile.agent_revision_id,
            child_effective_config=profile.effective_config,
            connector_connection_selections=profile.connector_connection_selections,
            mcp_connection_selections=profile.mcp_connection_selections,
            child_thread_id=self._thread_id_factory(),
            child_run_id=self._run_id_factory(),
            relationship_id=self._relationship_id_factory(),
            recovery_budget=profile.recovery_budget,
            created_at=assume_utc(self._clock()),
            cancellation_policy=profile.cancellation_policy,
            result_visibility=profile.result_visibility,
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
        profile = self._profile(plan)
        parent, parent_state = await self._parent(authority)
        source_state = await self._states.read(
            source.run.organization_id,
            source.run.id,
            expected_thread_id=source.thread.id,
        )
        return prepare_child_resume(
            parent_run=parent,
            parent_state=parent_state,
            parent_run_attempt_id=authority.run_attempt_id,
            parent_run_attempt_generation=authority.fence,
            parent_agent_instance_id=plan.parent.parent_agent_instance_id,
            subagent_name=source.relationship.subagent_name,
            delegated_input=delegated_input,
            child_definition_id=profile.definition_id,
            child_agent_id=profile.agent_id,
            child_agent_revision_id=profile.agent_revision_id,
            child_effective_config=profile.effective_config,
            connector_connection_selections=profile.connector_connection_selections,
            mcp_connection_selections=profile.mcp_connection_selections,
            source_relationship=source.relationship,
            source_parent_run=source.parent_run,
            source_thread=source.thread,
            source_run=source.run,
            source_state=source_state.envelope,
            child_run_id=self._run_id_factory(),
            relationship_id=self._relationship_id_factory(),
            recovery_budget=profile.recovery_budget,
            created_at=assume_utc(self._clock()),
            cancellation_policy=profile.cancellation_policy,
            result_visibility=profile.result_visibility,
        )

    def _profile(self, plan: SubagentDelegationPlan) -> ChildRunAdmissionProfile:
        name = plan.child.declaration.name
        profile = self._profiles.get(name)
        if profile is None:
            raise SubagentOperatorError(
                "subagent_admission_profile_missing",
                "The parent Attempt has no frozen admission profile for this child",
            )
        if profile.definition_id != plan.child.definition.definition_id:
            raise SubagentOperatorError(
                "subagent_definition_conflict",
                "Harness child identity no longer matches its frozen admission profile",
            )
        return profile

    async def _parent(self, authority: AttemptContext) -> tuple[Run, RunStateEnvelope]:
        async with short_session(self._sessions) as database:
            parent, _, _ = await read_attempt_authority(database, authority, assume_utc(self._clock()))
            parent_resource = parent.to_resource()
        stored = await self._states.read(
            authority.organization_id,
            authority.run_id,
            expected_thread_id=authority.thread_id,
        )
        return parent_resource, stored.envelope


__all__ = [
    "ChildRunAdmissionProfile",
    "ProfileChildRunAdmissionPreparer",
]
