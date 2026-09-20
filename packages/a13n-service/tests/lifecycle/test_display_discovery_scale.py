"""Exercise discovery against retained SQL history, with real Redis and objects."""

from datetime import timedelta
from time import monotonic
from unittest.mock import AsyncMock

import anyio
import pytest
from a13n_service.interactions.models import RunRecord
from a13n_service.run_stream import RedisRunStream, RunDisplayStore
from a13n_service.run_stream.display_candidates import DisplayCandidates
from a13n_service.run_stream.display_consumer import RunDisplayConsumer
from a13n_service.storage import transaction
from a13n_service.temporal import utc_now
from sqlalchemy import String, cast, event, func, insert, literal, select, text, true
from tests.hooks.support import RUN_ID, seed_run_and_secret
from tests.interactions.conftest import ORGANIZATION_ID, THREAD_ID
from tests.run_stream.support import activate_stream

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("history_count", [10_000, 100_000])
@pytest.mark.parametrize("worker_count", [1, 2])
@pytest.mark.parametrize("any_redis_client", ["redis"], indirect=True)
async def test_settled_history_does_not_increase_discovery_or_object_work(
    lifecycle_interaction_sessions,
    any_redis_client,
    object_store,
    monkeypatch,
    history_count,
    worker_count,
    record_property,
):
    sessions = lifecycle_interaction_sessions
    await seed_run_and_secret(sessions)
    now = utc_now()
    table = RunRecord.__table__
    source = table.alias("source")
    numbers = select(func.generate_series(1, history_count).label("n")).subquery()
    overrides = {
        "status": "failed",
        "sealed_at": now - timedelta(days=7),
        "display_settled_at": now - timedelta(days=6),
        "failure_json": {"code": "fixture_failure", "message": "Historical failure."},
    }
    values = [
        func.concat("run_", func.lpad(cast(numbers.c.n, String), 16, "0"))
        if column.name == "id"
        else literal(overrides[column.name], type_=column.type)
        if column.name in overrides
        else source.c[column.name]
        for column in table.columns
    ]
    async with transaction(sessions) as database:
        await database.execute(
            insert(table).from_select(
                list(table.columns.keys()),
                select(*values).select_from(source.join(numbers, true())).where(source.c.id == RUN_ID),
            )
        )
        await database.execute(text("ANALYZE runs"))

    captured = []

    def capture(_connection, _cursor, statement, parameters, _context, _executemany):
        captured.append((statement, parameters))

    engine = sessions.kw["bind"]
    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    candidates = DisplayCandidates(sessions)
    try:
        assert len(await candidates.page(lane="active", after=None, limit=32, now=now)) == 1
        assert await candidates.page(lane="recovery", after=None, limit=32, now=now) == ()
        assert await candidates.page(lane="cleanup", after=None, limit=32, now=now) == ()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    assert len(captured) == 3
    plans = []
    async with engine.connect() as connection:
        for statement, parameters in captured:
            result = await connection.exec_driver_sql(
                "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + statement, parameters
            )
            plan = result.scalar_one()[0]
            plans.append(plan)
            assert plan["Plan"]["Actual Rows"] <= 1
            assert plan["Plan"]["Shared Hit Blocks"] + plan["Plan"]["Shared Read Blocks"] <= 32
    assert "ix_runs_display_active" in str(plans[0])
    assert all("ix_runs_display_unsettled" in str(plan) for plan in plans[1:])

    stream = RedisRunStream(any_redis_client)
    await activate_stream(stream, ORGANIZATION_ID, RUN_ID, THREAD_ID)
    store = RunDisplayStore(object_store)
    reads = AsyncMock(wraps=store.read)
    monkeypatch.setattr(store, "read", reads)
    started = monotonic()

    async def consume():
        consumer = RunDisplayConsumer(DisplayCandidates(sessions), stream, store)
        assert await consumer.consume_once(lane="active") == 1
        assert await consumer.consume_once(lane="recovery") == 0
        assert await consumer.consume_once(lane="cleanup") == 0

    async with anyio.create_task_group() as tasks:
        for _ in range(worker_count):
            tasks.start_soon(consume)
    assert reads.await_count == worker_count
    measurements = {
        "history_runs": history_count,
        "workers": worker_count,
        "object_reads": reads.await_count,
        "consumption_ms": round((monotonic() - started) * 1000, 2),
        "discovery_ms": [plan["Execution Time"] for plan in plans],
        "discovery_buffers": [plan["Plan"]["Shared Hit Blocks"] + plan["Plan"]["Shared Read Blocks"] for plan in plans],
    }
    record_property("display_discovery", measurements)
    print(measurements)
