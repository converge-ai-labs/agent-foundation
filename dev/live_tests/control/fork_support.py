"""Evidence that one real operation progresses before its peer's barrier opens."""

import asyncio
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

logger = logging.getLogger(__name__)


async def completed_source(journey, *, environment=None):
    agent = await journey.control_agent()
    case = await journey.case()
    source = await journey.start(case, agent_id=agent["agent"]["id"], environment=environment)
    return await journey.live.finish(source["run_id"])


@asynccontextmanager
async def paused_command(journey, path, body, *, point="fork.initial_before", **match):
    barrier = journey.arm("paused-" + uuid4().hex, point, role="control", **match)
    task = asyncio.create_task(
        journey.live.http.post(path, json=body, headers={"Idempotency-Key": uuid4().hex}, timeout=90)
    )
    try:
        await journey.reached(barrier)
        assert_paused(barrier, task)
        yield barrier, task
    finally:
        journey.release(barrier)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def assert_paused(barrier, task=None):
    assert (barrier / "hit-1.json").exists()
    assert not (barrier / "release").exists(), "Peer was released before independent progress was proved"
    assert not (barrier / "finished-1.json").exists(), "Peer's pause timed out or was cancelled"
    if task is not None:
        assert not task.done(), "Peer request finished before the independence assertion"
    logger.info("Peer barrier remains active: %s", barrier.name)


async def accepted_reply(journey, task):
    async with asyncio.timeout(30):
        reply = await task
    assert reply.status_code == 202, reply.text
    payload = reply.json()
    receipt = payload.get("run", payload)
    journey.live.track(receipt)
    return receipt


async def assert_fork(journey, source, receipt, case):
    result = await journey.live.finish(receipt["run_id"])
    assert result["lineage_kind"] == "fork" and result["parent_run_id"] == source["id"]
    assert result["thread_id"] != source["thread_id"] and result["session_id"] == source["session_id"]
    assert result["output_text"] == case["token"]
    assert (await journey.state(result["id"]))["receipts"] == []
    assert await journey.inbox(result["thread_id"]) == [] and await journey.queued(result) == []
    assert await journey.live.run(source["id"]) == source
    return result
