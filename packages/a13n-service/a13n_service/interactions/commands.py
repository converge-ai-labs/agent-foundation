"""Compose cohesive interaction use cases for protocol and process entry points."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.assets.catalog import AssetCatalog
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.temporal import Clock, utc_now

from .acceptance import RunAcceptanceService
from .active_commands import ActiveRunCommands
from .command_preparation import CommandInput
from .continuation_commands import ContinuationCommands
from .domain import ExecutionBudget
from .inbox import ThreadInboxStore
from .initialization import NewRunPolicy
from .objects import RunPayloadStore, RunStateStore
from .outcomes import RunOutcomeService
from .queue_commands import QueuedRunCommands
from .run_commands import RunCommands


class InteractionCommands:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        invocations: AgentInvocationResolver,
        acceptance: RunAcceptanceService,
        states: RunStateStore,
        assets: AssetCatalog,
        endpoint_policy: EndpointPolicy,
        *,
        outcomes: RunOutcomeService,
        inbox: ThreadInboxStore,
        payloads: RunPayloadStore,
        execution_max_attempts: int = 3,
        max_handoffs: int = 2,
        queue_name: str = "default",
        priority: int = 0,
        clock: Clock = utc_now,
    ) -> None:
        self.acceptance = acceptance
        inputs = CommandInput(sessions, assets, endpoint_policy)
        policy = NewRunPolicy(
            priority=priority,
            queue_name=queue_name,
            execution_budget=ExecutionBudget(
                policy_version="1", max_attempts=execution_max_attempts, max_handoffs=max_handoffs
            ),
        )
        self.runs = RunCommands(sessions, invocations, acceptance, states, inputs, policy, clock=clock)
        self.continuations = ContinuationCommands(sessions, acceptance, states, payloads, inputs, clock=clock)
        self.active = ActiveRunCommands(sessions, states, outcomes, inbox, inputs, acceptance=acceptance, clock=clock)
        self.queued = QueuedRunCommands(sessions, invocations, acceptance, states, inputs, policy, clock=clock)
