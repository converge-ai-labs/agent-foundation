"""Thread creation and Attempt claiming, with real contention and no execution loop."""

from contextlib import asynccontextmanager
from uuid import uuid4

from a13n_service.iam import AuthenticatedActor, PrincipalRef
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, ThreadRecord
from a13n_service.interactions.scheduling import ClaimedAttempt
from a13n_service.interactions.thread_creation import allocate_thread
from a13n_service.interactions.thread_domain import CreateThreadRequest
from a13n_service.storage import short_session
from sqlalchemy import func, select

from .connection_preparation import prepare_pg_connections
from .operations import Operation


class ClaimLost(Exception):
    """Classify the scheduler's ordinary empty claim result separately from errors."""


async def measure_threads(benchmark, config, service):
    actor = AuthenticatedActor(
        principal=PrincipalRef(principal_type="user", principal_id=service.lab.config["user_id"]),
        auth_method="session",
        credential_id="ses_live_test",
        boundary_workspace_id=service.lab.config["workspace_id"],
    )

    async def create_thread(*, key):
        return await allocate_thread(
            service.sessions,
            actor=actor,
            workspace_id=service.lab.config["workspace_id"],
            body=CreateThreadRequest(),
            idempotency_key=key,
        )

    for state in config.thread_connection_states:
        for concurrency in config.concurrency:

            @asynccontextmanager
            async def prepare(count, connection_state=state):
                await prepare_pg_connections(service.engine, state=connection_state, count=count)
                calls = []
                for _ in range(count):
                    key = uuid4().hex

                    async def call(identity=key):
                        return await create_thread(key=identity)

                    async def verify(result):
                        async with short_session(service.sessions) as database:
                            row = await database.get(ThreadRecord, result.id)
                            assert row.current_run_id is None and row.version == 1 and row.queue_version == 0
                            assert (row.next_delivery_sequence, row.pending_count, row.pending_bytes) == (1, 0, 0)

                    calls.append(Operation(call, verify))
                yield calls

            await benchmark.measure(
                "thread.create",
                concurrency,
                prepare,
                configuration={
                    "pg_pool_size": config.pg_pool_size,
                    "pg_connection_state": state,
                    "pg_prewarm_connections": concurrency if state == "warm" else 0,
                    "model_requests": 0,
                },
                boundary="allocate_thread including PG connection acquisition, authorization, Session/Thread/idempotency writes and PG commit; connection state reset before every wave",
            )


async def measure_allocation(benchmark, config, service):
    if "thread.create" in config.scenarios:
        await measure_threads(benchmark, config, service)
    for name in ("attempt.claim", "attempt.claim_same_run"):
        if name not in config.scenarios:
            continue
        for concurrency in config.concurrency:

            @asynccontextmanager
            async def prepare(count, scenario=name):
                calls = []
                shared = scenario.endswith("same_run")
                run_ids = [await service.accepted()] * count if shared else await service.prepare_runs(count)
                await prepare_pg_connections(service.engine, state="warm", count=count)
                winners = []
                for run_id in run_ids:

                    async def call(identifier=run_id):
                        result = await service.scheduler.claim(identifier, service.worker())
                        if result is None:
                            raise ClaimLost()
                        return result

                    async def verify(result, identifier=run_id):
                        if isinstance(result, ClaimLost):
                            return
                        assert isinstance(result, ClaimedAttempt)
                        winners.append(identifier)
                        async with short_session(service.sessions) as database:
                            run = await database.get(RunRecord, identifier)
                            attempts = await database.scalar(
                                select(func.count())
                                .select_from(RunAttemptRecord)
                                .where(RunAttemptRecord.run_id == identifier)
                            )
                            assert attempts == 1 and run.current_run_attempt_id == result.attempt.id

                    calls.append(Operation(call, verify, ClaimLost if shared else None, allow_success=shared))
                yield calls
                assert len(winners) == (1 if shared else count)

            await benchmark.measure(
                name,
                concurrency,
                prepare,
                configuration={
                    "pg_pool_size": config.pg_pool_size,
                    "pg_connection_state": "warm",
                    "http_pool_size": config.http_pool_size,
                    "model_requests": 0,
                },
                boundary="AttemptScheduler.claim including native locks and PG commit; accepted Run already prepared; no scan, Worker startup, S3 claim or model entry",
            )
