"""Control-owned maintenance of durable subagent lifecycles."""

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
    sessions = shared.storage.sessions
    signals = RedisThreadControlSignals(shared.storage.redis)
    outcomes = RunOutcomeService(
        sessions, RunPayloadStore(shared.storage.objects), lifecycle=shared.lifecycle, control_signals=signals
    )
    return SubagentMaintenance(
        ChildCancellationReconciler(sessions, outcomes),
        AsyncSubagentResultPublisher(sessions, replay, signals=signals),
        AsyncSubagentSuccessorReconciler(sessions, RunStateStore(shared.storage.objects), replay, signals=signals),
        poll_interval_seconds=settings.subagent_reconcile_poll_interval_seconds,
    )
