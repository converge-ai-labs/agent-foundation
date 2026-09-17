"""Control-owned maintenance of durable subagent lifecycles."""

from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.interactions.inbox import RedisThreadControlSignals
from a13n_service.interactions.objects import RunPayloadStore, RunStateStore
from a13n_service.interactions.outcomes import RunOutcomeService
from a13n_service.process.runtime import SharedRuntime
from a13n_service.run_stream import RunReplayStore
from a13n_service.settings import Settings
from a13n_service.subagents.cancellation import ChildCancellationReconciler
from a13n_service.subagents.maintenance import SubagentMaintenance
from a13n_service.subagents.results import AsyncSubagentResultPublisher
from a13n_service.subagents.successors import AsyncSubagentSuccessorReconciler


def build_subagent_maintenance(
    settings: Settings, shared: SharedRuntime, replay: RunReplayStore
) -> SubagentMaintenance:
    if shared.memory_behaviors is None:
        raise RuntimeError("Execution memory behavior composition is required")
    sessions = shared.storage.sessions
    signals = RedisThreadControlSignals(shared.storage.redis)
    outcomes = RunOutcomeService(
        sessions, RunPayloadStore(shared.storage.objects), lifecycle=shared.lifecycle, control_signals=signals
    )
    return SubagentMaintenance(
        ChildCancellationReconciler(sessions, outcomes),
        AsyncSubagentResultPublisher(sessions, replay, signals=signals),
        AsyncSubagentSuccessorReconciler(
            sessions,
            RunStateStore(shared.storage.objects),
            replay,
            bindings=shared.memory_behaviors,
            coordination=ConnectionCoordination(shared.storage.redis),
            lifecycle=shared.lifecycle,
            signals=signals,
        ),
        poll_interval_seconds=settings.subagents.reconcile_poll_interval_seconds,
        batch_limit=settings.control.recovery_batch_limit,
        item_timeout_seconds=settings.control.recovery_item_timeout_seconds,
    )
