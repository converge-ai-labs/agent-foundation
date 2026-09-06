import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from a13n_service.storage import short_session
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.anyio


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
