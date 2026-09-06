from __future__ import annotations

import pytest
from a13n_service.interactions.inbox import RedisThreadControlSignals
from a13n_service.interactions.wakeups import ThreadControlWakeups
from fakeredis.aioredis import FakeRedis

from .conftest import THREAD_ID
from .test_attempt_executor import _context

pytestmark = pytest.mark.anyio


async def test_wakeups_poll_without_signals_and_ack_only_the_delivered_batch():
    async with FakeRedis() as redis:
        context = _context(THREAD_ID)
        signals = RedisThreadControlSignals(redis)
        wakeups = ThreadControlWakeups(signals, context, poll_interval_seconds=0.001)
        batch = await wakeups.receive()
        assert batch == ()
        await wakeups.acknowledge(batch)
        await signals.publish(organization_id=context.organization_id, thread_id=context.thread_id)
        batch = await wakeups.receive()
        assert len(batch) == 1
        with pytest.raises(RuntimeError):
            await wakeups.receive()
        with pytest.raises(ValueError):
            await wakeups.acknowledge(object())
        await wakeups.acknowledge(batch)
        with pytest.raises(ValueError):
            await wakeups.acknowledge(batch)
        assert await wakeups.receive() == ()
