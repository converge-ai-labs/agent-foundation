"""Observe real queue scans and separate prepared HTTP/background contenders."""

from contextvars import ContextVar
from functools import wraps


def install(faults, options):
    from a13n_service.interactions.acceptance import RunAcceptanceService
    from a13n_service.interactions.queue import QueuedSubmissionStore
    from a13n_service.interactions.queue_commands import QueuedRunCommands
    from a13n_service.interactions.queue_recovery import QueueRecovery

    consumer = ContextVar("live_queue_consumer", default=None)
    original_consume = QueuedRunCommands.consume_queued

    @wraps(original_consume)
    async def consume(self, **kwargs):
        owner = "recovery" if kwargs["idempotency_key"] is None else "explicit"
        token = consumer.set(owner)
        succeeded = False
        try:
            result = await original_consume(self, **kwargs)
            succeeded = True
            return result
        finally:
            consumer.reset(token)
            await faults.reach(
                "queue.consume_finished", consumer=owner, thread_id=kwargs["thread_id"], succeeded=succeeded
            )

    QueuedRunCommands.consume_queued = consume
    original_initial = RunAcceptanceService._publish_initial

    @wraps(original_initial)
    async def initial(self, run, state):
        result = await original_initial(self, run, state)
        if owner := consumer.get():
            # Initial state is already durable; the acceptance transaction has
            # not begun. Distinct candidates must compete through production SQL.
            await faults.reach(
                "queue.consume_prepared",
                consumer=owner,
                thread_id=run.thread_id,
                run_id=run.id,
                parent_run_id=run.parent_run_id or "",
            )
        return result

    RunAcceptanceService._publish_initial = initial
    original_enqueue = QueuedSubmissionStore.enqueue

    @wraps(original_enqueue)
    async def enqueue(self, **kwargs):
        await faults.reach("queue.enqueue_prepared", thread_id=kwargs["thread_id"])
        return await original_enqueue(self, **kwargs)

    QueuedSubmissionStore.enqueue = enqueue
    original_scan = QueueRecovery.scan

    @wraps(original_scan)
    async def scan(self):
        result = await original_scan(self)
        await faults.reach(
            "queue.scanned", examined=result.examined, completed=result.completed, deferred=result.deferred
        )
        return result

    QueueRecovery.scan = scan
    if limits := options.get("queue"):
        original_init = QueuedSubmissionStore.__init__

        @wraps(original_init)
        def queue(self, *args, **kwargs):
            return original_init(self, *args, **{**kwargs, **limits})

        QueuedSubmissionStore.__init__ = queue
