"""Case 11: SIGTERM drains an owned Worker and does not claim queued work."""

import asyncio
import signal

import pytest

pytestmark = pytest.mark.anyio


async def test_worker_sigterm_completes_or_hands_off_and_stops_claiming(round_two):
    lab, live = round_two, round_two.client
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    await lab.wait_evidence(case, "checkpoint_ready", run_id=receipt["run_id"])
    queued_case = await live.case("basic")
    queued = await live.start(queued_case)
    assert await lab.attempts(queued["run_id"]) == [], "Single Worker exceeded its one-slot capacity"
    owner = lab.workers[0]
    lab.send(owner, signal.SIGTERM)
    await lab.wait_unready(owner)
    await live.release(case)
    async with asyncio.timeout(30):
        await owner.wait()
    assert owner.returncode == 0, "Worker did not exit cleanly"
    assert await lab.attempts(queued["run_id"]) == [], "Draining Worker claimed another Run"
    old = (await lab.attempts(receipt["run_id"]))[0]
    assert old["status"] in {"yielded", "succeeded"}, old
    if old["status"] == "yielded":
        assert old["yield_reason"] == "service_drain" and old["failure"] is None
    await lab.start_worker()
    await live.finish(receipt["run_id"])
    await live.finish(queued["run_id"])
    assert (await lab.attempts(receipt["run_id"]))[0] == old
    assert (await live.evidence(case))["effects"] == 1
