"""Case 7: cancel both model I/O and a running tool; retain the cancelled outcome."""

import pytest

from .stream import assert_stream

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize(
    "scenario,started,closed",
    [
        ("interrupt_model", "model_started", "model_closed"),
        ("interrupt_tool", "tool_started", "tool_closed"),
    ],
)
async def test_interrupt_active_execution(live, scenario, started, closed):
    case = await live.case(scenario)
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await live.wait_evidence(case, started)
    assert (await live.run(run_id))["status"] == "running"
    assert await live.interrupt(run_id) is not None, "Run settled before Interrupt was accepted"
    cancelled = await live.finish(run_id, "cancelled")
    # Check teardown before releasing the gate; releasing it must not be what stops the work.
    await live.wait_evidence(case, closed)
    await live.release(case)
    events = await live.events(run_id)
    assert_stream(events, run_id, "cancelled")
    assert await live.run(run_id) == cancelled
    assert not (await live.evidence(case))["output"], "Interrupted work published its success side effect"
    # A following Run proves the Worker remains usable after cancellation.
    following = await live.start(await live.case("basic"))
    await live.finish(following["run_id"])
