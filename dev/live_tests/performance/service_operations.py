"""Time native Service transitions and verify their durable outcomes separately."""

from contextlib import asynccontextmanager
from datetime import timedelta

from a13n_service.interactions.control_models import ThreadInboxRecord
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.queue import QueuedSubmissionConflict
from a13n_service.storage import short_session

from .allocation_operations import measure_allocation
from .operations import Operation


async def operation(service, name, prepared, *, size):
    if name == "run.complete":
        candidate = service.checkpoint(prepared, size=size)

        async def call():
            stored = await service.execution.publish_checkpoint(
                prepared.authority, service.states, prepared.state, candidate
            )
            return await service.outcomes.commit_state_outcome(prepared.authority, stored)

        async def verify(result):
            assert result.run_status.value == "completed"
            async with short_session(service.sessions) as database:
                run = (await database.get(RunRecord, prepared.run.id)).to_resource()
                attempt = await database.get(RunAttemptRecord, prepared.authority.run_attempt_id)
                thread = await database.get(ThreadRecord, prepared.thread.id)
                assert run.status.value == "completed" and attempt.status == "succeeded"
                assert run.current_run_attempt_id is None and thread.head_run_id == run.id
            stored = await service.states.read_run(run)
            assert stored.envelope == candidate
            assert run.sealed_state.digest_sha256 == stored.digest_sha256

        return Operation(call, verify)

    if name == "steer.consume":
        receipt = await service.append(prepared)
        candidate = service.checkpoint(prepared, receipt=receipt)
        stored = await service.execution.publish_checkpoint(
            prepared.authority, service.states, prepared.state, candidate
        )

        async def call():
            return await service.reconciler.confirm_inbox_receipts(prepared.authority, stored)

        async def verify(result):
            async with short_session(service.sessions) as database:
                row = await database.get(ThreadInboxRecord, receipt.steer_id)
                assert row.status == "consumed" and row.consumed_by_run_id == prepared.run.id
                assert row.consumed_state_digest_sha256 == stored.digest_sha256
                counter = await database.get(ThreadRecord, prepared.thread.id)
                assert counter.pending_count == 0 and counter.pending_bytes == 0

        return Operation(call, verify)

    if name.startswith("steer.append"):

        async def call():
            return await service.append(prepared)

        async def verify(result):
            status = await service.inbox.get_steer(
                organization_id=service.organization, run_id=prepared.run.id, steer_id=result.steer_id
            )
            assert status.status.value == "pending"

        return Operation(call, verify)

    if name.startswith("queue.enqueue"):
        store = service.queue
        rejected = name.endswith("full")

        async def call():
            return await service.enqueue(prepared, store=store)

        async def verify(result):
            if rejected:
                assert "capacity" in str(result)
                rows = await store.list(organization_id=service.organization, thread_id=prepared.thread.id, limit=256)
                assert tuple(row.queued_submission_id for row in rows.items) == prepared.queued_ids
                assert len(rows.items) == 256
            else:
                row = await store.get(
                    organization_id=service.organization,
                    queued_submission_id=result.queued_submission.queued_submission_id,
                )
                assert row.state.value == "queued" and row.consumed_run_id is None
            async with short_session(service.sessions) as database:
                thread = await database.get(ThreadRecord, prepared.thread.id)
                assert thread.current_run_id == prepared.run.id and thread.version == prepared.thread.version

        return Operation(call, verify, QueuedSubmissionConflict if rejected else None)

    if name == "attempt.heartbeat":
        async with short_session(service.sessions) as database:
            previous = await database.get(RunAttemptRecord, prepared.authority.run_attempt_id)
            previous_version, previous_expiry = previous.version, previous.lease_expires_at

        async def call():
            return await service.execution.heartbeat(prepared.authority, lease_duration=timedelta(seconds=300))

        async def verify(result):
            async with short_session(service.sessions) as database:
                row = await database.get(RunAttemptRecord, prepared.authority.run_attempt_id)
                assert row.status == "running" and row.lease_expires_at is not None
                assert row.version == previous_version + 1 == result.attempt_version
                assert row.lease_expires_at == result.lease_expires_at and row.lease_expires_at > previous_expiry

        return Operation(call, verify)
    raise ValueError(f"Unknown Service operation: {name}")


async def measure_service(benchmark, config, service):
    await measure_allocation(benchmark, config, service)
    boundaries = {
        "run.complete": "publish_checkpoint (PG authority reads + S3 conditional PUT/HEAD), then commit_state_outcome (PG terminal transaction and lifecycle intents); ready outcome and claimed writer prepared before timing; empty queue",
        "steer.append": "ThreadInboxStore.append_steer: canonical input to committed PG pending row; no Redis signal, model, delivery or HTTP middleware",
        "steer.append_same_thread": "Same Thread concurrent append_steer calls, including native lock contention; end at committed PG pending rows",
        "steer.consume": "DatabaseThreadInboxReconciler.confirm_inbox_receipts: one pending row to committed consumed, including authority locks/counter updates; durable S3 receipt prepared before timing",
        "queue.enqueue": "QueuedSubmissionStore.enqueue: validated intent to committed queued row; active Run; no Run creation or execution",
        "queue.enqueue_same_thread": "Same Thread concurrent enqueue calls including native lock contention; active Run; initially empty queue",
        "queue.enqueue_full": "QueuedSubmissionStore.enqueue: full-capacity rejection and transaction rollback on one shared Thread; capacity=256, existing rows=256",
        "attempt.heartbeat": "AttemptExecutionService.heartbeat: authority lock, lease update and PG commit",
    }
    for name, boundary in boundaries.items():
        if name not in config.scenarios:
            continue
        for size in config.payload_bytes if name == "run.complete" else [1024]:
            for concurrency in config.concurrency:
                full = None

                @asynccontextmanager
                async def prepare(count, scenario=name, payload_size=size):
                    nonlocal full
                    if scenario == "queue.enqueue_full":
                        if full is None:
                            full = await service.running()
                            for _ in range(256):
                                await service.enqueue(full)
                            rows = await service.queue.list(
                                organization_id=service.organization, thread_id=full.thread.id, limit=256
                            )
                            full.queued_ids = tuple(row.queued_submission_id for row in rows.items)
                        prepared = [full] * count
                    elif scenario.endswith("same_thread"):
                        prepared = [await service.running()] * count
                    else:
                        prepared = await service.prepare_runs(count, running=True)
                    yield [await operation(service, scenario, item, size=payload_size) for item in prepared]

                await benchmark.measure(
                    name,
                    concurrency,
                    prepare,
                    boundary=boundary,
                    configuration={
                        "input_or_history_padding_bytes": size,
                        "threads": 1 if name.endswith(("same_thread", "full")) else concurrency,
                        "pg_pool_size": config.pg_pool_size,
                        "s3_pool_size": config.s3_pool_size,
                        "queue_capacity": 256,
                        "model_requests": 0,
                        "http_middleware": False,
                    },
                )
