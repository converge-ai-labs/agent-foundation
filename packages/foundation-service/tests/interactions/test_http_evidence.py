"""Concurrent ordinary HTTP evidence must have one winner even at expiry."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from a13n_service.durable_operations.idempotency import (
    EvidenceScope,
    IdempotencyConflict,
    IdempotencyIdentity,
    digest_visible_ascii_key,
    load_evidence,
    new_evidence,
)
from a13n_service.durable_operations.models import IdempotencyEvidenceRecord
from a13n_service.storage import transaction
from sqlalchemy import select

from tests.interactions.conftest import NOW, ORGANIZATION_ID, USER_ID, WORKSPACE_ID

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("same_request", [True, False])
@pytest.mark.parametrize("organization_scope", [True, False])
async def test_postgresql_expired_key_replacement_has_one_winner(
    postgres_interaction_sessions, same_request, organization_scope
):
    scope = EvidenceScope(
        None if organization_scope else WORKSPACE_ID,
        "user",
        USER_ID,
        "test.command",
        WORKSPACE_ID,
        organization_id=ORGANIZATION_ID if organization_scope else None,
    )
    key = digest_visible_ascii_key("expiry-race")
    async with transaction(postgres_interaction_sessions) as database:
        database.add(
            new_evidence(
                organization_id=ORGANIZATION_ID,
                scope=scope,
                identity=IdempotencyIdentity(key, "0" * 64),
                result_kind="test",
                result_ref="expired",
                now=NOW,
            )
        )
    barrier = asyncio.Barrier(2)

    async def accept(index):
        await barrier.wait()
        identity = IdempotencyIdentity(key, "1" * 64 if same_request or index == 0 else "2" * 64)
        async with transaction(postgres_interaction_sessions) as database:
            replay = await load_evidence(database, scope=scope, identity=identity, now=NOW + timedelta(hours=24))
            if replay is not None:
                return replay.result_ref
            result = f"winner-{index}"
            database.add(
                new_evidence(
                    organization_id=ORGANIZATION_ID,
                    scope=scope,
                    identity=identity,
                    result_kind="test",
                    result_ref=result,
                    now=NOW + timedelta(hours=24),
                )
            )
            return result

    results = await asyncio.gather(accept(0), accept(1), return_exceptions=True)
    if same_request:
        assert results[0] == results[1]
    else:
        assert sum(isinstance(result, IdempotencyConflict) for result in results) == 1
        assert sum(isinstance(result, str) for result in results) == 1
    async with transaction(postgres_interaction_sessions) as database:
        records = (await database.scalars(select(IdempotencyEvidenceRecord))).all()
        assert len(records) == 1
        assert records[0].result_ref in {"winner-0", "winner-1"}
