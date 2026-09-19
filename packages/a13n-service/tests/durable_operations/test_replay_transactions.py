"""Read-only replay and atomic arbitration across concurrent business writes."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.durable_operations.idempotency import (
    EvidenceAlreadyCommitted,
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    find_evidence,
    insert_evidence,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.iam.models import WorkspaceRecord
from a13n_service.storage import short_session, transaction
from anyio import Event, create_task_group, fail_after
from sqlalchemy import select
from tests.interactions.conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID
from tests.interactions.conftest import interaction_sessions as interaction_sessions
from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio

SCOPE = EvidenceScope(WORKSPACE_ID, "user", USER_ID, "test.create", "parent", ORGANIZATION_ID)
IDENTITY = IdempotencyIdentity.from_request("test-key", {"input": "same"})


def _evidence(*, identity=IDENTITY, now=NOW, result="original"):
    return new_evidence(
        organization_id=ORGANIZATION_ID,
        scope=SCOPE,
        identity=identity,
        result_kind="test",
        result_ref=result,
        receipt={"result": result},
        now=now,
    )


async def test_preflight_reads_committed_receipt_without_waiting_for_final_lock(interaction_sessions):
    sessions = interaction_sessions
    async with transaction(sessions) as database:
        await insert_evidence(database, _evidence())
    async with transaction(sessions) as blocker:
        assert await load_evidence(blocker, scope=SCOPE, identity=IDENTITY, now=NOW) is not None
        with fail_after(2), capture_sql(sessions) as statements:
            async with short_session(sessions) as database:
                result = await find_evidence(database, scope=SCOPE, identity=IDENTITY, now=NOW)
                assert result.result_ref == "original"
        assert len(statements) == 1
        assert "FOR UPDATE" not in statements[0] and "pg_advisory" not in statements[0]


@pytest.mark.parametrize("offset", [-1, 0, 1])
async def test_preflight_expiry_never_deletes_or_reserves_evidence(interaction_sessions, offset):
    sessions = interaction_sessions
    evidence = _evidence()
    async with transaction(sessions) as database:
        await insert_evidence(database, evidence)
    now = NOW + timedelta(hours=24, microseconds=offset)
    with capture_sql(sessions) as statements:
        async with short_session(sessions) as database:
            result = await find_evidence(database, scope=SCOPE, identity=IDENTITY, now=now)
            assert (result is not None) == (offset < 0)
    assert len(statements) == 1 and statements[0].startswith("SELECT")
    async with short_session(sessions) as database:
        assert await database.get(IdempotencyEvidenceRecord, evidence.id) is not None
        # Organization-boundary evidence is not a Workspace receipt.
        assert (
            await find_evidence(database, scope=replace(SCOPE, workspace_id=None), identity=IDENTITY, now=NOW) is None
        )


async def test_expired_replacement_rolls_back_with_business_transaction(interaction_sessions):
    sessions = interaction_sessions
    original = _evidence()
    async with transaction(sessions) as database:
        await insert_evidence(database, original)
    changed = IdempotencyIdentity.from_request("test-key", {"input": "changed"})
    expiry = NOW + timedelta(hours=24)
    with pytest.raises(RuntimeError, match="business failure"):
        async with transaction(sessions) as database:
            await insert_evidence(database, _evidence(identity=changed, now=expiry, result="replacement"))
            raise RuntimeError("business failure")
    async with short_session(sessions) as database:
        assert await database.get(IdempotencyEvidenceRecord, original.id) is not None
    with capture_sql(sessions) as statements:
        async with transaction(sessions) as database:
            await insert_evidence(database, _evidence(identity=changed, now=expiry, result="replacement"))
    assert len(statements) == 1 and "ON CONFLICT" in statements[0]
    async with short_session(sessions) as database:
        assert (await find_evidence(database, scope=SCOPE, identity=changed, now=expiry)).result_ref == "replacement"
        with pytest.raises(IdempotencyConflict):
            await find_evidence(database, scope=SCOPE, identity=IDENTITY, now=expiry)


@pytest.mark.parametrize("different_input", [False, True])
async def test_final_arbitration_rolls_back_loser_after_both_preflights_miss(interaction_sessions, different_input):
    sessions = interaction_sessions
    ready = Event()
    arrivals = 0
    committed, replayed, conflicts = [], [], []

    async def submit(index):
        nonlocal arrivals
        identity = (
            IdempotencyIdentity.from_request("test-key", {"input": "changed"})
            if different_input and index == 1
            else IDENTITY
        )
        async with short_session(sessions) as database:
            assert await find_evidence(database, scope=SCOPE, identity=identity, now=NOW) is None
        arrivals += 1
        if arrivals == 2:
            ready.set()
        await ready.wait()
        result_id = f"business-{index}"
        try:
            async with transaction(sessions) as database:
                database.add(
                    WorkspaceRecord(
                        id=result_id,
                        organization_id=ORGANIZATION_ID,
                        name=result_id,
                        key=result_id,
                        created_at=NOW,
                        updated_at=NOW,
                    )
                )
                await database.flush()
                await insert_evidence(database, _evidence(identity=identity, result=result_id))
            committed.append(result_id)
        except EvidenceAlreadyCommitted:
            try:
                async with short_session(sessions) as database:
                    result = await find_evidence(database, scope=SCOPE, identity=identity, now=NOW)
                    replayed.append(result.result_ref)
            except IdempotencyConflict:
                conflicts.append(result_id)

    with fail_after(10):
        async with create_task_group() as tasks:
            tasks.start_soon(submit, 0)
            tasks.start_soon(submit, 1)
    assert len(committed) == 1
    assert len(conflicts) == int(different_input)
    assert replayed == ([] if different_input else committed)
    async with short_session(sessions) as database:
        assert (
            list(await database.scalars(select(WorkspaceRecord.id).where(WorkspaceRecord.id.like("business-%"))))
            == committed
        )
        evidence = (await database.scalars(select(IdempotencyEvidenceRecord))).one()
        assert evidence.result_ref == committed[0]
