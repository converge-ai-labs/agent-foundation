"""Connection release is one fenced update, including after lock contention."""

from dataclasses import replace
from datetime import timedelta

import pytest
from a13n_service.connectivity.transports.leases import ConnectionLeases
from a13n_service.connectivity.transports.models import EventConnectionRecord
from a13n_service.storage import short_session, transaction
from a13n_service.temporal import utc_now
from anyio import create_task_group, fail_after, sleep
from sqlalchemy import select, text

from tests.sql_capture import capture_sql

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("mismatch", [None, "key", "owner", "generation"])
async def test_release_is_one_update_and_preserves_other_owners(connectivity_sessions, mismatch):
    sessions = connectivity_sessions
    leases = ConnectionLeases(sessions, "worker")
    claim = await leases.claim("connection", {})
    supplied = claim if mismatch is None else replace(claim, **{mismatch: 99 if mismatch == "generation" else "wrong"})
    before = utc_now()
    with capture_sql(sessions) as statements:
        await leases.release(supplied)
    assert len(statements) == 1 and statements[0].startswith("UPDATE")
    async with short_session(sessions) as database:
        row = await database.get(EventConnectionRecord, claim.key)
        assert row.owner == claim.owner and row.generation == claim.generation
        if mismatch is None:
            assert row.state == "disconnected" and before <= row.lease_expires_at <= utc_now()
        else:
            assert row.state == "connecting" and row.lease_expires_at > before


async def test_waiting_release_does_not_disconnect_successor(connectivity_sessions):
    sessions = connectivity_sessions
    leases = ConnectionLeases(sessions, "worker")
    claim = await leases.claim("connection", {})
    with fail_after(5):
        async with create_task_group() as tasks:
            async with transaction(sessions) as blocker:
                row = await blocker.scalar(
                    select(EventConnectionRecord).where(EventConnectionRecord.key == claim.key).with_for_update()
                )
                tasks.start_soon(leases.release, claim)
                while True:
                    async with short_session(sessions) as observer:
                        blocked = await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE datname = current_database() AND wait_event_type = 'Lock' AND query LIKE 'UPDATE connectivity_event_connections%')"
                            )
                        )
                    if blocked:
                        break
                    await sleep(0.01)
                row.owner = "successor"
                row.generation += 1
                row.state = "connected"
                row.lease_expires_at = utc_now() + timedelta(seconds=30)
    async with short_session(sessions) as database:
        row = await database.get(EventConnectionRecord, claim.key)
        assert row.owner == "successor" and row.generation == claim.generation + 1
        assert row.state == "connected" and row.lease_expires_at > utc_now()
