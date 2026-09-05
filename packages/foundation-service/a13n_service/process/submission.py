"""Canonical input-command composition shared by Gateway and Connectivity roles."""

from a13n_service.agents.invocation_resolution import AgentInvocationResolver
from a13n_service.assets.service import AssetService
from a13n_service.endpoint_policy import EndpointPolicy
from a13n_service.hooks import InlineHookValidator
from a13n_service.interactions.acceptance import RunAcceptanceService
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.inbox import RedisThreadControlSignals, ThreadInboxStore
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


def build_input_commands(
    settings: Settings,
    shared: SharedRuntime,
    invocations: AgentInvocationResolver,
    assets: AssetService,
    inline_hooks: InlineHookValidator,
) -> InteractionCommands:
    states = RunStateStore(shared.storage.objects)
    payloads = RunPayloadStore(shared.storage.objects)
    signals = RedisThreadControlSignals(shared.storage.redis)
    return InteractionCommands(
        shared.storage.sessions,
        invocations,
        RunAcceptanceService(shared.storage.sessions, states, payloads, inline_hooks, lifecycle=shared.lifecycle),
        states,
        assets,
        EndpointPolicy(),
        outcomes=RunOutcomeService(
            shared.storage.sessions, payloads, control_signals=signals, lifecycle=shared.lifecycle
        ),
        inbox=ThreadInboxStore(shared.storage.sessions, signals=signals),
        payloads=payloads,
        recovery_max_attempts=settings.gateway_run_recovery_max_attempts,
        max_handoffs=settings.gateway_run_max_handoffs,
        queue_name=settings.gateway_run_queue_name,
        priority=settings.gateway_run_priority,
    )
