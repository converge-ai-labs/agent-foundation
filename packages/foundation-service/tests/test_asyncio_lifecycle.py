"""Foundation's asynchronous test resources share a package-lifetime runner."""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(scope="module")
def observed_loops() -> list[asyncio.AbstractEventLoop]:
    # This fixture is synchronous: it must not keep AnyIO's runner alive itself.
    return []


@pytest.mark.anyio
@pytest.mark.parametrize("iteration", range(2))
async def test_foundation_tests_share_a_live_event_loop(
    iteration: int,
    observed_loops: list[asyncio.AbstractEventLoop],
) -> None:
    loop = asyncio.get_running_loop()
    for previous in observed_loops:
        assert not previous.is_closed()
        assert previous is loop
        # Model a database worker completing a Future on an earlier test's loop.
        completed = previous.create_future()
        await asyncio.to_thread(previous.call_soon_threadsafe, completed.set_result, iteration)
        assert await completed == iteration
    observed_loops.append(loop)
