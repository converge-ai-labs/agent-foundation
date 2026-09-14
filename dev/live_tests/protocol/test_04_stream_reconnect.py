"""Case 4: disconnect while a real tool is blocked, then resume after an SSE cursor."""

import pytest

from .stream import assert_stream, stream_order

pytestmark = pytest.mark.anyio


async def test_stream_disconnect_does_not_cancel_run(live):
    case = await live.case("stream")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await live.wait_evidence(case, "tool_started", run_id=run_id)
    prefix = []
    async with live.stream(run_id) as stream:
        async for event in stream:
            prefix.append(event)
            if event.kind == "agui.tool_call_start":
                break
    assert prefix[-1].kind == "agui.tool_call_start"
    assert (await live.run(run_id))["status"] == "running"
    await live.release(case)
    await live.finish(run_id)
    suffix = await live.events(run_id, after=prefix[-1].cursor)
    assert suffix and all(stream_order(event.cursor) > stream_order(prefix[-1].cursor) for event in suffix)
    complete = await live.events(run_id)
    assert prefix + suffix == complete, "Reconnect lost, duplicated, or changed retained events"
    assert_stream(complete, run_id)
    assert await live.events(run_id, after=complete[-1].cursor) == []
