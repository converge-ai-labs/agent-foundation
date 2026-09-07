"""Control-owned Thread recovery, separate from Worker execution."""

from functools import partial

from a13n_service.background import PeriodicTask
from a13n_service.interactions.commands import InteractionCommands
from a13n_service.interactions.inbox import RedisThreadControlSignals
from a13n_service.interactions.objects import RunStateStore
from a13n_service.interactions.recovery import QueueRecovery
from a13n_service.process.background import BackgroundTask
from a13n_service.process.runtime import SharedRuntime
from a13n_service.run_stream import RunReplayStore
from a13n_service.settings import Settings
from a13n_service.subagents.results import AsyncSubagentResultPublisher
from a13n_service.subagents.successors import AsyncSubagentSuccessorReconciler


def build_recovery_tasks(
    settings: Settings,
    shared: SharedRuntime,
    commands: InteractionCommands,
    replays: RunReplayStore,
) -> tuple[BackgroundTask, ...]:
    signals = RedisThreadControlSignals(shared.storage.redis)
    queue = QueueRecovery(
        shared.storage.sessions,
        commands,
        batch_limit=settings.control_recovery_batch_limit,
        item_timeout_seconds=settings.control_recovery_item_timeout_seconds,
    )
    results = AsyncSubagentResultPublisher(shared.storage.sessions, replays, signals=signals)
    successors = AsyncSubagentSuccessorReconciler(
        shared.storage.sessions, RunStateStore(shared.storage.objects), replays, signals=signals
    )
    scans = (
        ("queued_submission_recovery", queue.scan),
        (
            "async_result_publication",
            partial(
                results.scan,
                limit=settings.control_recovery_batch_limit,
                item_timeout_seconds=settings.control_recovery_item_timeout_seconds,
            ),
        ),
        (
            "async_result_successor",
            partial(
                successors.scan,
                limit=settings.control_recovery_batch_limit,
                item_timeout_seconds=settings.control_recovery_item_timeout_seconds,
            ),
        ),
    )
    return tuple(
        BackgroundTask(
            name,
            PeriodicTask(
                name,
                scan,
                interval_seconds=settings.control_recovery_poll_interval_seconds,
                timeout_seconds=(settings.control_recovery_item_timeout_seconds + 1)
                * settings.control_recovery_batch_limit,
            ).run,
        )
        for name, scan in scans
    )
