"""Bind the standard async tools to a fresh Harness Run and its durable Attempt."""

from a13n_harness.capabilities import SubagentCapability, SubagentOperatorContext
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.interactions.domain import Run
from a13n_service.interactions.inbox import ThreadInboxStore
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.interactions.ports.memory import ExecutionBindings
from a13n_service.interactions.run_control import RunAttemptControl

from .acceptance import ChildRunAcceptanceService
from .admission import ChildRunAdmissionPreparer
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
        *,
        lifecycle: LifecycleWriter,
        bindings: ExecutionBindings,
    ) -> None:
        self._sessions = sessions
        self._admission = ChildRunAdmissionPreparer(sessions, states)
        self._acceptance = ChildRunAcceptanceService(sessions, states, payloads, lifecycle=lifecycle, bindings=bindings)
        self._inbox = inbox
        self._outcomes = outcomes

    def capability(self, *, run: Run, authority: RunAttemptControl) -> SubagentCapability:
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
            self._admission,
            self._acceptance,
            self._inbox,
            self._outcomes,
            parent_context=parent_context,
        )
        return SubagentCapability(async_enabled=True, operator=operator)
