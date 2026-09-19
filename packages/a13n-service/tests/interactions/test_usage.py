from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_harness.usage import BoundedRequestUsage, ModelUsageRecord
from a13n_service.interactions.attempts import (
    AttemptAuthorityError,
    AttemptExecutionService,
    AttemptMutationError,
    AttemptUsageExceeded,
)
from a13n_service.interactions.models import RunAttemptRecord, RunRecord, RunUsageRecord
from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt
from a13n_service.storage import short_session, transaction
from anyio import create_task_group
from sqlalchemy import select

from tests.lifecycle_support import test_lifecycle_writer
from tests.sql_capture import capture_sql

from .conftest import NOW
from .test_attempt_execution import _accept_root, _authority, _worker

pytestmark = pytest.mark.anyio


def receipt(ordinal=0, *, input_tokens=12, output_tokens=3):
    return ModelUsageRecord(
        record_id=f"usage-receipt-{ordinal}",
        run_id="harness-usage",
        response_ordinal=ordinal,
        agent_instance_id="instance-usage",
        response_state="complete",
        response_timestamp=NOW,
        request_usage=BoundedRequestUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        cost_source="unknown",
        pricing_status="disabled",
    )


async def setup_usage(sessions, objects, *, admit_request=True):
    _, run, _ = await _accept_root(sessions, objects)
    scheduler = AttemptScheduler(sessions, clock=lambda: NOW + timedelta(seconds=1), lifecycle=test_lifecycle_writer())
    claim = await scheduler.claim(run.id, _worker())
    assert isinstance(claim, ClaimedAttempt)
    authority = _authority(claim)
    execution = AttemptExecutionService(
        sessions, clock=lambda: NOW + timedelta(seconds=2), lifecycle=test_lifecycle_writer()
    )
    preparation = await execution.commit_preparation_success(authority)
    await execution.enter_harness(authority, preparation=preparation, harness_run_id="harness-usage")
    if admit_request:
        await execution.increment_model_request(authority)
    return execution, authority


async def read_usage(sessions, authority):
    async with short_session(sessions) as session:
        attempt = await session.get(RunAttemptRecord, authority.run_attempt_id)
        run = await session.get(RunRecord, authority.run_id)
        records = (await session.scalars(select(RunUsageRecord))).all()
        return dict(attempt.usage_json), [record.record_json for record in records], run.to_resource()


async def test_incremental_terminal_overlap_and_conflicts(interaction_sessions, interaction_object_store):
    sessions = interaction_sessions
    execution, authority = await setup_usage(sessions, interaction_object_store)
    first, second = receipt(), receipt(1, input_tokens=7, output_tokens=5)
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[first, first])
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[first, second])
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[first, second])
    usage, records, _ = await read_usage(sessions, authority)
    assert (usage["model_requests"], usage["input_tokens"], usage["output_tokens"]) == (1, 19, 8)
    assert len(records) == 2
    with pytest.raises(AttemptMutationError, match="Conflicting"):
        await execution.ingest_usage(
            authority, harness_run_id="harness-usage", records=[receipt(2), receipt(input_tokens=99)]
        )
    assert (await read_usage(sessions, authority))[:2] == (usage, records)


@pytest.mark.parametrize("fault", ["token", "harness", "thread", "attempt"])
async def test_receipts_require_original_authority(interaction_sessions, interaction_object_store, fault):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store)
    fields = {"token": {"lease_token": "wrong"}, "thread": {"thread_id": "other"}, "attempt": {"attempt_number": 99}}
    invalid = replace(authority, **fields.get(fault, {}))
    with pytest.raises(AttemptAuthorityError):
        await execution.ingest_usage(
            invalid, harness_run_id="other" if fault == "harness" else "harness-usage", records=[receipt()]
        )
    usage, records, _ = await read_usage(interaction_sessions, authority)
    assert not records and usage["input_tokens"] == 0


@pytest.mark.parametrize("status", ["failed", "cancelled", "succeeded", "yielded"])
async def test_late_receipts_are_retained_without_rewriting_terminal_run(
    interaction_sessions, interaction_object_store, status
):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store)
    # Build terminal database states directly to exercise the ingestion boundary for
    # every terminal Attempt kind, independently of its lifecycle command.
    async with transaction(interaction_sessions) as session:
        attempt = await session.get(RunAttemptRecord, authority.run_attempt_id)
        attempt.status = status
        attempt.finished_at = NOW + timedelta(seconds=3)
        if status == "failed":
            attempt.failure_json = {"code": "test_failure", "message": "Test failure"}
        if status == "yielded":
            attempt.yield_reason = "service_drain"
    before = await read_usage(interaction_sessions, authority)
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[receipt()])
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[receipt()])
    usage, records, run = await read_usage(interaction_sessions, authority)
    assert usage == before[0] and run == before[2] and len(records) == 1


async def test_expired_owner_can_only_add_evidence(interaction_sessions, interaction_object_store):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store)
    execution = AttemptExecutionService(
        interaction_sessions, clock=lambda: NOW + timedelta(days=1), lifecycle=test_lifecycle_writer()
    )
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[receipt()])
    usage, records, _ = await read_usage(interaction_sessions, authority)
    assert usage["input_tokens"] == 0 and len(records) == 1


async def test_concurrent_delivery_counts_once(interaction_sessions, interaction_object_store):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store)

    async def deliver(offset):
        await execution.ingest_usage(
            authority, harness_run_id="harness-usage", records=[receipt(i) for i in range(offset, offset + 50)]
        )

    async with create_task_group() as group:
        for offset in (0, 25, 50, 0):
            group.start_soon(deliver, offset)
    usage, records, _ = await read_usage(interaction_sessions, authority)
    assert usage["input_tokens"] == 1200 and usage["output_tokens"] == 300 and len(records) == 100


async def test_batch_conflict_rolls_back_all_receipts(interaction_sessions, interaction_object_store):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store)
    before = await read_usage(interaction_sessions, authority)
    with pytest.raises(AttemptMutationError, match="Conflicting"):
        await execution.ingest_usage(
            authority,
            harness_run_id="harness-usage",
            records=[receipt(1), receipt(2), receipt(1, input_tokens=99)],
        )
    assert await read_usage(interaction_sessions, authority) == before


@pytest.mark.parametrize("size", [1, 100])
async def test_usage_batch_has_bounded_database_round_trips(interaction_sessions, interaction_object_store, size):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store)
    batch = [receipt(i) for i in range(size)]
    with capture_sql(interaction_sessions) as statements:
        await execution.ingest_usage(authority, harness_run_id="harness-usage", records=batch + batch)
    assert len(statements) == 6, statements
    assert sum("FROM run_usage_records" in sql for sql in statements) == 1
    assert sum(sql.startswith("INSERT INTO run_usage_records") for sql in statements) == 1
    usage, records, _ = await read_usage(interaction_sessions, authority)
    assert len(records) == size
    assert (usage["input_tokens"], usage["output_tokens"]) == (12 * size, 3 * size)
    with capture_sql(interaction_sessions) as statements:
        await execution.ingest_usage(authority, harness_run_id="harness-usage", records=batch)
    assert len(statements) == 4, statements
    assert not any(sql.startswith(("INSERT", "UPDATE")) for sql in statements)
    assert (await read_usage(interaction_sessions, authority))[:2] == (usage, records)


async def test_incurred_usage_survives_budget_exhaustion(interaction_sessions, interaction_object_store):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store)
    async with transaction(interaction_sessions) as session:
        run = await session.get(RunRecord, authority.run_id)
        run.max_usage_json = {"input_tokens": 5}
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[receipt()])
    with pytest.raises(AttemptMutationError, match="budget"):
        await execution.increment_model_request(authority)
    usage, records, _ = await read_usage(interaction_sessions, authority)
    assert usage["input_tokens"] == 12 and usage["model_requests"] == 1 and len(records) == 1


async def test_nested_model_receipts_keep_identity_without_double_counting(
    interaction_sessions, interaction_object_store
):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store)
    nested = receipt(1, input_tokens=7, output_tokens=5).model_copy(update={"run_id": "inline-harness"})
    await execution.increment_model_request(authority)
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[nested])
    await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[receipt(), nested])
    usage, records, _ = await read_usage(interaction_sessions, authority)
    assert (usage["model_requests"], usage["input_tokens"], usage["output_tokens"]) == (2, 19, 8)
    assert {record["run_id"] for record in records} == {"harness-usage", "inline-harness"}


@pytest.mark.parametrize("field", ["input_tokens", "output_tokens"])
@pytest.mark.parametrize(
    "ceiling,prior,current,allowed",
    [
        (0, 0, 0, False),
        (10, 0, 9, True),
        (10, 0, 10, False),
        (10, 0, 11, False),
        (10, 6, 3, True),
        (10, 6, 4, False),
        (10, 10, 0, False),
        (None, 0, 10, True),
    ],
)
async def test_request_admission_counts_current_and_previous_attempt_tokens(
    interaction_sessions, interaction_object_store, field, ceiling, prior, current, allowed
):
    execution, authority = await setup_usage(interaction_sessions, interaction_object_store, admit_request=current > 0)
    async with transaction(interaction_sessions) as session:
        run = await session.get(RunRecord, authority.run_id)
        run.max_usage_json = {field: ceiling}
        run.usage_charged_json = {field: prior}
    if current:
        usage_receipt = receipt(input_tokens=0, output_tokens=0).model_copy(
            update={"request_usage": BoundedRequestUsage(**{field: current})}
        )
        await execution.ingest_usage(authority, harness_run_id="harness-usage", records=[usage_receipt])
    before = await read_usage(interaction_sessions, authority)
    if allowed:
        await execution.increment_model_request(authority)
        usage, records, run = await read_usage(interaction_sessions, authority)
        assert usage == {**before[0], "model_requests": before[0]["model_requests"] + 1}
        assert (records, run) == before[1:]
    else:
        with pytest.raises(AttemptUsageExceeded, match="budget"):
            await execution.increment_model_request(authority)
        assert await read_usage(interaction_sessions, authority) == before
