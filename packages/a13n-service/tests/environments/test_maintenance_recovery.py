from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import psycopg
import pytest
from a13n_service.environments import maintenance
from anyio import current_time, fail_after
from sqlalchemy.exc import OperationalError

pytestmark = pytest.mark.anyio


async def test_failed_database_scan_closes_scope_and_next_sweep_recovers(monkeypatch, caplog):
    loop = maintenance.EnvironmentMaintenanceLoop(Mock(), interval_seconds=0.02)
    calls, closed = [], []

    @asynccontextmanager
    async def session_scope(factory):
        calls.append(current_time())
        try:
            if len(calls) == 1:
                raise OperationalError(None, None, psycopg.OperationalError("connection refused"))
            assert closed == [1]
            session = Mock()
            session.scalars = AsyncMock(return_value=())
            loop.drain()
            yield session
        finally:
            closed.append(len(calls))

    monkeypatch.setattr(maintenance, "short_session", session_scope)
    with fail_after(2):
        await loop.run()
    assert closed == [1, 2]
    assert calls[1] - calls[0] >= 0.02
    assert "environment_maintenance_database_unavailable" in caplog.text


async def test_mixed_maintenance_failure_is_fatal(monkeypatch):
    loop = maintenance.EnvironmentMaintenanceLoop(Mock())
    failure = ExceptionGroup(
        "sweep",
        [
            OperationalError(None, None, psycopg.OperationalError("connection refused")),
            ValueError("invalid maintenance state"),
        ],
    )
    run_once = AsyncMock(side_effect=failure)
    monkeypatch.setattr(loop, "run_once", run_once)
    with pytest.raises(ExceptionGroup) as caught:
        await loop.run()
    assert caught.value is failure
    run_once.assert_awaited_once()
