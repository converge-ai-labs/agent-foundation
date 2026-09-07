from contextlib import asynccontextmanager

import pytest
from a13n_service.interactions.attempt_resources import attempt_resource_stack
from anyio import CancelScope, create_task_group, get_cancelled_exc_class, sleep, sleep_forever

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def resource(trace):
    async with create_task_group():
        try:
            trace.append("open")
            yield
        finally:
            await sleep(0)
            trace.append("closed")


@pytest.mark.parametrize("failure", ["none", "preparation", "execution", "cancel"])
async def test_attempt_resources_close_nested_task_groups_in_order(failure):
    trace = []
    try:
        with CancelScope() as owner:
            async with attempt_resource_stack(cleanup_timeout_seconds=1) as stack:
                await stack.enter_async_context(resource(trace))
                if failure == "preparation":
                    raise ValueError("prepare failed")
                trace.append("execute")
                if failure == "execution":
                    raise ValueError("execute failed")
                if failure == "cancel":
                    owner.cancel()
                    await sleep_forever()
            trace.append("finalize")
    except* ValueError:
        assert failure in {"preparation", "execution"}
    assert trace.count("closed") == 1
    assert ("finalize" in trace) is (failure == "none")
    if failure == "none":
        assert trace.index("closed") < trace.index("finalize")


@pytest.mark.parametrize("suppresses_cancellation", [False, True])
async def test_cleanup_timeout_prevents_successful_finalization(suppresses_cancellation):
    async def close():
        try:
            await sleep_forever()
        except get_cancelled_exc_class():
            if not suppresses_cancellation:
                raise

    with pytest.raises(TimeoutError, match="resource cleanup timed out"):
        async with attempt_resource_stack(cleanup_timeout_seconds=0.01) as stack:
            stack.push_async_callback(close)
