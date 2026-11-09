"""Credential timestamp maintenance must not serialize authentication on one row."""

import asyncio
import logging
import re
from datetime import timedelta

import pytest
from a13n_service.infra.crypto import secret_hash
from a13n_service.infra.db import now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.infra.ids import new_object_id
from a13n_service.tenancy import authenticate as auth
from a13n_service.tenancy.credentials import issue_token
from a13n_service.tenancy.tables import ApiKeyRow, TokenRow
from sqlalchemy import event, func, select, update

pytestmark = pytest.mark.anyio


async def issued(runtime, tenant, kind):
    async with transaction(runtime.storage) as session:
        if kind == "session":
            row, secret = await issue_token(session, tenant.principal_id, "session", seconds=300)
        else:
            secret = "test-api-key"
            row = ApiKeyRow(
                id=new_object_id("key"),
                organization_id=tenant.organization_id,
                workspace_id=tenant.workspace_id,
                principal_id=tenant.principal_id,
                name="test",
                secret_hash=secret_hash(secret),
                created_by_id=tenant.principal_id,
            )
            session.add(row)
        await session.flush()
        return row.id, secret


async def authenticate(runtime, secret, kind):
    return await auth.authenticate_secret(runtime.storage, runtime.access, secret, kind, session_seconds=600)


@pytest.fixture
def writes(runtime):
    counts = []

    def executed(conn, cursor, statement, parameters, context, executemany):
        if re.search(r"\bUPDATE\s+(tokens|api_keys)\s+SET\b", statement, re.IGNORECASE):
            counts.append(cursor.rowcount)

    engine = runtime.storage.engine.sync_engine
    event.listen(engine, "after_cursor_execute", executed)
    try:
        yield counts
    finally:
        event.remove(engine, "after_cursor_execute", executed)


@pytest.mark.parametrize("kind", ["session", "key"])
@pytest.mark.parametrize("concurrency", [128, 256])
async def test_burst_coalesces_to_one_write(runtime, tenant, writes, kind, concurrency, caplog):
    credential_id, secret = await issued(runtime, tenant, kind)
    caplog.set_level(logging.DEBUG, logger=auth.__name__)
    gate = asyncio.Event()

    async def request():
        await gate.wait()
        return await authenticate(runtime, secret, kind)

    tasks = [asyncio.create_task(request()) for _ in range(concurrency)]
    gate.set()
    results = await asyncio.wait_for(asyncio.gather(*tasks), 20)
    assert all(r.credential_id == credential_id and r.principal.id == tenant.principal_id for r in results)
    assert all(count >= 0 for count in writes) and sum(writes) == 1
    await authenticate(runtime, secret, kind)
    assert sum(writes) == 1
    assert any(r.message == "Credential touch attempted" and r.result == "updated" for r in caplog.records)
    assert secret not in caplog.text
    async with short_session(runtime.storage) as session:
        if kind == "key":
            row = await session.get_one(ApiKeyRow, credential_id)
            assert row.last_used_at is not None and row.version == 1
        else:
            row = await session.get_one(TokenRow, credential_id)
            assert row.expires_at > await now(session) + timedelta(seconds=550)


@pytest.mark.parametrize("kind", ["session", "key"])
async def test_stale_reads_cannot_repeat_a_completed_touch(runtime, tenant, writes, monkeypatch, kind):
    _, secret = await issued(runtime, tenant, kind)
    original = auth._live_credential
    ready = asyncio.Event()
    count = 0

    async def read(*args, **kwargs):
        nonlocal count
        credential = await original(*args, **kwargs)
        count += 1
        if count == 2:
            ready.set()
        await ready.wait()
        return credential

    monkeypatch.setattr(auth, "_live_credential", read)
    await asyncio.wait_for(asyncio.gather(*(authenticate(runtime, secret, kind) for _ in range(2))), 5)
    assert sum(writes) == 1


@pytest.mark.parametrize("kind", ["session", "key"])
async def test_locked_row_does_not_block_authentication_and_later_touch_succeeds(runtime, tenant, writes, kind):
    credential_id, secret = await issued(runtime, tenant, kind)
    row_type = TokenRow if kind == "session" else ApiKeyRow
    async with transaction(runtime.storage) as holder:
        await holder.scalar(select(row_type.id).where(row_type.id == credential_id).with_for_update())
        # The holder cannot exit until all callers finish. Waiting for its lock
        # rather than skipping it fails this test, regardless of machine speed.
        results = await asyncio.wait_for(asyncio.gather(*(authenticate(runtime, secret, kind) for _ in range(16))), 5)
        assert all(r.credential_id == credential_id for r in results)
        assert sum(writes) == 0
    await authenticate(runtime, secret, kind)
    assert sum(writes) == 1


@pytest.mark.parametrize("kind", ["session", "key"])
@pytest.mark.parametrize("change", ["revoke", "expire"])
async def test_change_after_initial_lookup_prevents_touch_and_next_authentication(
    runtime, tenant, monkeypatch, kind, change
):
    credential_id, secret = await issued(runtime, tenant, kind)
    row_type = TokenRow if kind == "session" else ApiKeyRow
    original = auth.principal_for

    async def principal(*args, **kwargs):
        result = await original(*args, **kwargs)
        async with transaction(runtime.storage) as other:
            statement = update(row_type).where(row_type.id == credential_id)
            await other.execute(
                statement.values(**{"revoked_at" if change == "revoke" else "expires_at": func.clock_timestamp()})
            )
        return result

    monkeypatch.setattr(auth, "principal_for", principal)
    # The current request already authenticated before revocation/expiry; retain
    # that existing in-flight behavior, but never extend its obsolete credential.
    await authenticate(runtime, secret, kind)
    async with short_session(runtime.storage) as session:
        row = await session.get_one(row_type, credential_id)
        if change == "expire":
            assert row.expires_at <= await now(session)
        elif kind == "session":
            assert row.expires_at < await now(session) + timedelta(seconds=400)
        if kind == "key":
            assert row.last_used_at is None
    with pytest.raises(ServiceError) as failure:
        await authenticate(runtime, secret, kind)
    assert failure.value.code == "unauthenticated"


async def test_nearly_expired_session_renews_when_uncontended(runtime, tenant):
    credential_id, secret = await issued(runtime, tenant, "session")
    async with transaction(runtime.storage) as session:
        await session.execute(
            update(TokenRow)
            .where(TokenRow.id == credential_id)
            .values(expires_at=func.clock_timestamp() + timedelta(seconds=5))
        )
    await authenticate(runtime, secret, "session")
    async with short_session(runtime.storage) as session:
        assert (await session.get_one(TokenRow, credential_id)).expires_at > await now(session) + timedelta(seconds=550)
