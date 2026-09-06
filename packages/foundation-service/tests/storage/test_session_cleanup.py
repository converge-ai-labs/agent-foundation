import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.storage import short_session, transaction
from a13n_service.storage.config import PostgreSQLConfig
from a13n_service.storage.relational import create_session_factory, create_sql_engine, limit_session_cleanup
from anyio import CancelScope, create_task_group, current_effective_deadline, current_time, fail_after, sleep_forever
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("scope", [short_session, transaction])
async def test_child_database_cleanup_cannot_outlive_its_owner_limit(scope):
    observed = []

    async def stalled_cleanup():
        observed.append(current_effective_deadline() - current_time())
        await sleep_forever()

    session = Mock(spec=AsyncSession)
    session.close = AsyncMock(side_effect=stalled_cleanup)
    session.begin = AsyncMock(return_value=Mock(rollback=AsyncMock(side_effect=stalled_cleanup)))

    async def cancelled_child():
        with pytest.raises(asyncio.CancelledError):
            async with scope(lambda: session, cleanup_timeout_seconds=10):
                raise asyncio.CancelledError()

    with limit_session_cleanup(0.01), limit_session_cleanup(1):
        async with create_task_group() as tasks:
            tasks.start_soon(cancelled_child)
    assert len(observed) == (2 if scope is transaction else 1)
    assert all(0 < remaining <= 0.01 for remaining in observed)
    session.close.assert_awaited_once()
    session.close.side_effect = lambda: observed.append(current_effective_deadline() - current_time())
    async with short_session(lambda: session):
        pass
    assert 1 < observed[-1] <= 5


async def test_cleanup_failure_does_not_replace_cancellation_and_invalidates_connection():
    session = Mock(spec=AsyncSession)
    session.close = AsyncMock(side_effect=RuntimeError("driver disconnected"))
    session.invalidate = AsyncMock()
    cancellation = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError) as result:
        async with short_session(lambda: session):
            raise cancellation
    assert result.value is cancellation
    session.invalidate.assert_awaited_once()
    assert "RuntimeError" in cancellation.__notes__[0]


async def test_cleanup_failure_without_an_existing_error_is_not_hidden():
    session = Mock(spec=AsyncSession)
    session.close = AsyncMock(side_effect=RuntimeError("driver disconnected"))
    session.invalidate = AsyncMock()
    with pytest.raises(RuntimeError, match="driver disconnected"):
        async with short_session(lambda: session):
            pass
    session.invalidate.assert_awaited_once()


async def test_driver_error_during_active_cancellation_preserves_cancellation():
    session = Mock(spec=AsyncSession)
    session.close = AsyncMock()
    with CancelScope() as scope:
        async with short_session(lambda: session):
            scope.cancel()
            raise DBAPIError(None, None, RuntimeError("driver cancellation cleanup"))
    assert scope.cancelled_caught
    session.close.assert_awaited_once()


async def test_driver_error_without_active_cancellation_is_not_hidden():
    session = Mock(spec=AsyncSession)
    session.close = AsyncMock()
    failure = DBAPIError(None, None, RuntimeError("driver operation"))
    with pytest.raises(DBAPIError) as result:
        async with short_session(lambda: session):
            raise failure
    assert result.value is failure
    session.close.assert_awaited_once()


async def test_postgresql_cancelled_pool_ping_preserves_timeout_and_retires_connection(pg_url, monkeypatch):
    engine = create_sql_engine(PostgreSQLConfig(url=pg_url, pool_size=1, max_overflow=0))
    sessions = create_session_factory(engine)
    try:
        async with short_session(sessions) as session:
            await session.execute(text("SELECT 1"))
        with monkeypatch.context() as patch:
            patch.setattr(engine.sync_engine.dialect, "_dialect_specific_select_one", "SELECT pg_sleep(10)")
            with pytest.raises(TimeoutError), fail_after(0.05):
                async with short_session(sessions) as session:
                    await session.execute(text("SELECT 1"))
        with fail_after(3):
            async with short_session(sessions) as session:
                assert await session.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()
