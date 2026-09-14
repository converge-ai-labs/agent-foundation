"""Real PG: 8/32 heartbeat/usage writers queue behind cancel or planned handoff.

The test owns one temporary blocking Thread lock to prove the production
transactions actually wait in PostgreSQL. No production transaction is patched,
and no Run/Attempt rows are fabricated. Admission/cancellation still use HTTP.
"""

import asyncio
import logging
from datetime import timedelta
from uuid import uuid4

import pytest
from a13n_service.configuration.sources import load_settings
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptContext,
    AttemptExecutionService,
    AttemptLease,
)
from a13n_service.interactions.domain import RunAttemptYieldReason, RunUsage
from a13n_service.interactions.lifecycle import LifecycleWriter
from a13n_service.interactions.models import ThreadRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt, WorkerClaim
from a13n_service.storage import open_storage, transaction
from a13n_service.storage.config import FilesystemConfig, PostgreSQLConfig
from sqlalchemy import select

from .contention_support import assert_lifecycle, contention_case, contention_metrics, inbox_budget, lifecycle, query

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


async def claim_running(journey, sessions, execution, *, build):
    source = await journey.start(await journey.case())
    authority = await enter_claim(journey, sessions, execution, source["run_id"], build=build)
    return source, authority


async def enter_claim(journey, sessions, execution, run_id, *, build):
    duration = timedelta(seconds=60)
    claim = await AttemptScheduler(sessions, lifecycle=LifecycleWriter()).claim(
        run_id,
        WorkerClaim(
            organization_id=journey.live.config["organization_id"],
            worker_id="worker-" + uuid4().hex,
            worker_build_id=build,
            lease_duration=duration,
            handoff_preference_window=timedelta(seconds=1),
        ),
    )
    assert isinstance(claim, ClaimedAttempt)
    authority = AttemptContext(
        organization_id=claim.attempt.organization_id,
        thread_id=claim.thread_id,
        run_id=run_id,
        run_attempt_id=claim.attempt.id,
        attempt_number=claim.attempt.attempt_number,
        lease_token=claim.lease_token,
        worker_id=claim.attempt.worker_id,
        worker_build_id=build,
        lease_duration=duration,
        lease=AttemptLease(claim.attempt.lease_expires_at),
        renewal_interval=duration / 3,
        renewal_timeout=duration / 6,
        reconciliation_timeout=timedelta(seconds=5),
        cleanup_timeout=timedelta(seconds=5),
    )
    preparation = await execution.commit_preparation_success(authority)
    await execution.enter_harness(authority, preparation=preparation, harness_run_id="harness-" + uuid4().hex)
    return authority


@pytest.mark.parametrize("writers", [8, 32], ids=lambda n: f"writers-{n}")
@pytest.mark.parametrize("transition", ["interrupt", "handoff"])
@pytest.mark.parametrize("first", ["execution", "control"])
@contention_case(
    "{writers} heartbeat/usage writers and one {transition} transaction wait on the same Thread lock; {first} enters first"
)
async def test_heartbeat_usage_and_control_serialize_without_lost_charge_or_authority(
    control, writers, transition, first, monkeypatch
):
    journey, live, lab = control, control.live, control.lab
    await lab.stop(lab.workers[0])
    settings = load_settings(environ=lab.environment).storage_settings()
    settings = settings.model_copy(
        update={
            "database": PostgreSQLConfig(
                url=lab.environment["A13N_SERVICE_DATABASE_URL"],
                pool_size=writers + 2,
                max_overflow=0,
                statement_timeout_seconds=5,
            ),
            "filesystem": FilesystemConfig(root=lab.root / "transaction-test-files"),
        }
    )
    # Use the same fixture-owned PG, Redis and S3 with the canonical runtime.
    # AWS credentials are SDK environment inputs, not Service settings fields.
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION", "NO_PROXY"):
        if key in lab.environment:
            monkeypatch.setenv(key, lab.environment[key])
        else:
            monkeypatch.delenv(key, raising=False)
    async with open_storage(settings) as storage:
        sessions = storage.sessions
        tasks = []
        try:
            execution = AttemptExecutionService(sessions, lifecycle=LifecycleWriter())
            source, authority = await claim_running(journey, sessions, execution, build="contention-original")
            # Broader than a bare two-party terminal race: cancellation must release
            # all pending reservations, while a planned handoff must retain them.
            steers = [await journey.steer(source["run_id"]) for _ in range(8)]
            before = await execution.validate(authority)
            thread = await live.thread(source["thread_id"])
            cancel_path, cancel_body = await journey.command(source, "interrupt")

            async def do_control_operation():
                if transition == "interrupt":
                    reply = await live.http.post(
                        cancel_path, json=cancel_body, headers={"Idempotency-Key": uuid4().hex}
                    )
                    assert reply.status_code == 202, reply.text
                    return reply
                return await execution.yield_attempt(authority, RunAttemptYieldReason.service_drain)

            async def control_operation():
                return await contention_metrics().call(
                    transition + "_database_lock_inclusive",
                    "Single control transaction behind the test-owned Thread lock",
                    do_control_operation,
                )

            async def do_write(index):
                if index % 2:
                    return await execution.add_usage(
                        authority, RunUsage(model_requests=1, input_tokens=7, output_tokens=3)
                    )
                return await execution.heartbeat(authority, lease_duration=timedelta(seconds=120))

            async def write(index):
                operation = "usage" if index % 2 else "heartbeat"
                return await contention_metrics().call(
                    operation + "_database_lock_inclusive",
                    f"{operation.title()} transaction {index + 1}/{writers} behind the test-owned Thread lock",
                    lambda: do_write(index),
                    expected_exceptions=(AttemptAuthorityError,) if first == "control" else (),
                )

            async def blocked():
                return (
                    await query(
                        journey,
                        "SELECT count(*) AS count FROM pg_stat_activity WHERE datname = current_database() "
                        "AND wait_event_type = 'Lock' AND cardinality(pg_blocking_pids(pid)) > 0",
                        (),
                    )
                )[0]["count"]

            async def wait_blocked(count):
                async with asyncio.timeout(2):
                    await live.wait(blocked, lambda value: value >= count, f"{count} real PostgreSQL lock waiters")

            # Test-owned coordination only; release before PostgreSQL's bounded
            # statement timeout. Each production call creates its own short session.
            async with transaction(sessions) as blocker:
                await blocker.scalar(
                    select(ThreadRecord).where(ThreadRecord.id == source["thread_id"]).with_for_update()
                )
                if first == "control":
                    control_task = asyncio.create_task(control_operation())
                    tasks.append(control_task)
                    await wait_blocked(1)
                writes = [asyncio.create_task(write(index)) for index in range(writers)]
                tasks.extend(writes)
                await wait_blocked(writers + int(first == "control"))
                if first == "execution":
                    control_task = asyncio.create_task(control_operation())
                    tasks.append(control_task)
                await wait_blocked(writers + 1)
                assert all(not task.done() for task in tasks)
                logger.info(
                    "PostgreSQL contention confirmed: writers=%s control=%s first=%s waiters=%s",
                    writers,
                    transition,
                    first,
                    writers + 1,
                )
            results = await asyncio.gather(*writes, return_exceptions=True)
            await control_task
            succeeded = [result for result in results if not isinstance(result, BaseException)]
            rejected = [result for result in results if isinstance(result, BaseException)]
            assert all(isinstance(result, AttemptAuthorityError) for result in rejected), rejected
            assert len(succeeded) == (writers if first == "execution" else 0)
            usage_writes = sum(not isinstance(result, BaseException) for result in results[1::2])
            expected_usage = {
                "model_requests": usage_writes,
                "input_tokens": 7 * usage_writes,
                "output_tokens": 3 * usage_writes,
            }
            evidence = await journey.execution(source["run_id"])
            for key, value in expected_usage.items():
                assert evidence["attempts"][0]["usage"][key] == value
                assert evidence["usage_charged"][key] == value
            assert evidence["current_run_attempt_id"] is None
            assert evidence["handoffs_completed"] == int(transition == "handoff")
            rows = await journey.inbox(source["thread_id"])
            assert len(rows) == len(steers) == 8
            assert all(row["status"] == ("superseded" if transition == "interrupt" else "pending") for row in rows)
            budget = await inbox_budget(journey, source["thread_id"])
            assert budget["pending_count"] == (0 if transition == "interrupt" else 8)
            if transition == "interrupt":
                assert budget["pending_bytes"] == 0
            # Late heartbeat/usage from the old authority cannot renew or add usage.
            snapshot = await journey.execution(source["run_id"])
            for operation in (
                lambda: execution.heartbeat(authority, lease_duration=timedelta(seconds=120)),
                lambda: execution.add_usage(authority, RunUsage(model_requests=1)),
            ):
                with pytest.raises(AttemptAuthorityError):
                    await operation()
            assert await journey.execution(source["run_id"]) == snapshot
            if succeeded:
                assert sorted(result.attempt_version for result in succeeded) == list(
                    range(before.attempt_version + 1, before.attempt_version + writers + 1)
                )
                assert max(result.lease_expires_at for result in succeeded) > before.lease_expires_at
            if transition == "handoff":
                assert (await live.thread(source["thread_id"]))["version"] == thread["version"]
                await enter_claim(journey, sessions, execution, source["run_id"], build="contention-replacement")
                await journey.post(*await journey.command(source, "interrupt"), expected=202)
                assert (await journey.execution(source["run_id"]))["usage_charged"] == snapshot["usage_charged"]
            await live.finish(source["run_id"], "cancelled")
            attempts = await lab.attempts(source["run_id"])
            assert len(attempts) == (2 if transition == "handoff" else 1)
            assert_lifecycle(await lifecycle(journey, [source["run_id"]]), source["run_id"], "cancelled", attempts)
            assert (await inbox_budget(journey, source["thread_id"]))["pending_count"] == 0
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
