"""Bind the standard async tools to a fresh Harness Run and its durable Attempt."""

from a13n_harness.capabilities import SubagentCapability, SubagentOperatorContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.interactions.domain import Run
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.run_control import RunAttemptControl

from .acceptance import ChildRunAcceptanceService
from .admission import ChildRunAdmissionProfile, ProfileChildRunAdmissionPreparer
from .operator import DurableSubagentOperator


class ServiceSubagents:
    """Share durable services while binding operator authority separately for each native Run."""

    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        states: RunStateStore,
        payloads: RunPayloadStore,
        inbox: ThreadInboxStore,
        outcomes: RunOutcomeService,
    ) -> None:
        self._sessions = sessions
        self._states = states
        self._acceptance = ChildRunAcceptanceService(sessions, states, payloads)
        self._inbox = inbox
        self._outcomes = outcomes

    def capability(self, *, run: Run, config: EffectiveAgentConfig, authority: RunAttemptControl) -> SubagentCapability:
        profiles = {}
        for edge in config.resolved_subagents:
            child = config.child_configs[edge.child_agent_revision_id]
            profiles[edge.name] = ChildRunAdmissionProfile(
                agent_id=edge.child_agent_id,
                agent_revision_id=edge.child_agent_revision_id,
                definition_id=f"agent-config-{child.revision_content_digest[:24]}",
                effective_config=child.effective_config,
                connector_connection_selections=child.connector_connection_selections,
                mcp_connection_selections=child.mcp_connection_selections,
                recovery_budget=run.recovery_budget,
            )
        admission = ProfileChildRunAdmissionPreparer(self._sessions, self._states, profiles)

        def parent_context() -> SubagentOperatorContext:
            identity = authority.harness_identity
            return SubagentOperatorContext(
                parent_thread_id=identity.thread_id,
                parent_run_id=identity.run_id,
                parent_agent_instance_id=run.thread_id,
                host_refs={"session_id": run.session_id, "thread_id": run.thread_id, "run_id": run.id},
            )

        operator = DurableSubagentOperator(
            self._sessions,
            authority,
            admission,
            self._acceptance,
            self._inbox,
            self._outcomes,
            parent_context=parent_context,
        )
        return SubagentCapability(async_enabled=True, operator=operator)
