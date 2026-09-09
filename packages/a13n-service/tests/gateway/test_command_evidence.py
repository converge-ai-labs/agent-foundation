"""Finite HTTP replay, original receipts, and acceptance rollback."""

from __future__ import annotations

from datetime import timedelta

import pytest
from a13n_service.durable_operations.idempotency import digest_visible_ascii_key
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.interactions.models import RunRecord
from a13n_service.storage import short_session
from a13n_service.storage.object_store import LocalObjectStore
from sqlalchemy import select

from tests.hooks.support import seed_hook_actor_access
from tests.interactions.conftest import NOW, WORKSPACE_ID

from .test_commands import _actor, _commands, _Freezing, _frozen, _Preparation, _request

pytestmark = pytest.mark.anyio


async def test_http_start_replay_expires_without_expiring_execution_identity(
    lifecycle_interaction_sessions,
    tmp_path,
) -> None:
    objects = await LocalObjectStore.create(tmp_path / "expiry-objects")
    now = NOW
    commands = _commands(
        lifecycle_interaction_sessions, objects, _Preparation(), _Freezing([_frozen()]), clock=lambda: now
    )
    await seed_hook_actor_access(lifecycle_interaction_sessions)
    request = _request("finite replay")
    first = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="expiry", request=request
    )
    now = NOW + timedelta(hours=24, microseconds=-1)
    assert (
        await commands.runs.start(actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="expiry", request=request)
        == first
    )
    now = NOW + timedelta(hours=24)
    second = await commands.runs.start(
        actor=_actor(), workspace_id=WORKSPACE_ID, idempotency_key="expiry", request=request
    )
    assert second.run_id != first.run_id
    async with short_session(lifecycle_interaction_sessions) as database:
        assert await database.get(RunRecord, first.run_id) is not None


async def test_binding_failure_rolls_back_run_lifecycle_and_http_receipt(
    lifecycle_interaction_sessions, tmp_path
) -> None:
    from a13n_service.durable_operations.models import OutboxRecord
    from a13n_service.lifecycle.models import LifecycleEventRecord

    objects = await LocalObjectStore.create(tmp_path / "rollback-objects")
    commands = _commands(lifecycle_interaction_sessions, objects, _Preparation(), _Freezing([_frozen()]))
    await seed_hook_actor_access(lifecycle_interaction_sessions)

    async def fail_binding(database, receipt):
        assert await database.get(RunRecord, receipt.run_id) is not None
        raise RuntimeError("binding rejected")

    with pytest.raises(RuntimeError, match="binding rejected"):
        await commands.runs.start(
            actor=_actor(),
            workspace_id=WORKSPACE_ID,
            idempotency_key="rollback",
            request=_request("atomic"),
            transaction_hook=fail_binding,
        )
    async with short_session(lifecycle_interaction_sessions) as database:
        for model in (RunRecord, LifecycleEventRecord, OutboxRecord, IdempotencyEvidenceRecord):
            assert (await database.scalars(select(model))).all() == []


@pytest.mark.anyio
async def test_expiry_cleanup_is_bounded_and_does_not_remove_live_evidence(lifecycle_interaction_sessions) -> None:
    from datetime import timedelta

    from a13n_service.durable_operations.idempotency import (
        EvidenceScope,
        IdempotencyIdentity,
        delete_expired_evidence,
        load_evidence,
        new_evidence,
    )
    from a13n_service.storage import transaction
    from sqlalchemy import select

    from tests.hooks.support import seed_hook_actor_access
    from tests.interactions.conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID

    await seed_hook_actor_access(lifecycle_interaction_sessions)
    scope = EvidenceScope(WORKSPACE_ID, "user", USER_ID, "test.accept", WORKSPACE_ID)
    async with transaction(lifecycle_interaction_sessions) as database:
        for index in range(4):
            database.add(
                new_evidence(
                    organization_id=ORGANIZATION_ID,
                    scope=scope,
                    identity=IdempotencyIdentity(digest_visible_ascii_key(str(index)), "a" * 64),
                    result_kind="test",
                    result_ref="receipt",
                    now=NOW if index < 3 else NOW + timedelta(hours=1),
                )
            )
    async with transaction(lifecycle_interaction_sessions) as database:
        assert await delete_expired_evidence(database, now=NOW + timedelta(hours=24), limit=2) == 2
    async with transaction(lifecycle_interaction_sessions) as database:
        assert len((await database.scalars(select(IdempotencyEvidenceRecord))).all()) == 2
        assert (
            await load_evidence(
                database,
                scope=scope,
                identity=IdempotencyIdentity(digest_visible_ascii_key("3"), "a" * 64),
                now=NOW + timedelta(hours=24),
            )
            is not None
        )
        assert await delete_expired_evidence(database, now=NOW + timedelta(hours=24), limit=2) == 1
