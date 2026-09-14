"""Spec 22/24: publisher replacement is an ordered observation, not a new Run."""

import logging
import signal

import pytest

from .hosted_client import HostedClient
from .stream_contract import assert_hosted_contract, assert_native_contract

pytestmark = pytest.mark.anyio


async def test_hosted_and_native_recovery_boundary_survives_reconnect(round_two):
    lab, live = round_two, round_two.client
    case = await live.case("checkpoint")
    hosted = HostedClient(live, case)
    prefix = []
    async with hosted.stream() as stream:
        async for event in stream:
            prefix.append(event)
            if event.data["type"] == "TOOL_CALL_RESULT":
                break
    run = await hosted.run()
    await live.wait_evidence(case, "checkpoint_ready", run_id=run["id"])
    await lab.stop(lab.workers[0], signal.SIGKILL)
    # The initial short lease drives the deliberate takeover. Give the replacement
    # ordinary headroom: this test asserts protocol boundaries, not lease races.
    lab.worker_environment["A13N_SERVICE_WORKER_LEASE_SECONDS"] = "60"
    await lab.start_worker()
    await live.wait(lambda: lab.attempts(run["id"]), lambda attempts: len(attempts) == 2, "replacement Attempt")

    async def checkpoint():
        evidence = await live.evidence(case)
        if evidence["checkpoint_requests"] < 2:
            current = await live.run(run["id"])
            assert current["status"] == "running", (
                f"Replacement ended before restoring checkpoint: {current['failure']}"
            )
        return evidence

    await live.wait(checkpoint, lambda evidence: evidence["checkpoint_requests"] >= 2, "restored checkpoint")
    boundary = []
    async with hosted.stream(after=prefix[-1].cursor) as stream:
        async for event in stream:
            boundary.append(event)
            if event.data.get("name") == "a13n.service.run_recovery":
                break
    recovery = boundary[-1]
    assert recovery.data["name"] == "a13n.service.run_recovery"
    await live.release(case)
    run = await live.finish(run["id"])
    suffix = await hosted.events(after=recovery.cursor)
    complete = await hosted.events()
    assert prefix + boundary + suffix == complete
    assert_hosted_contract(complete, hosted.body, "completed", private_ids=await hosted.private_ids())
    assert sum(event.data.get("name") == "a13n.service.run_recovery" for event in complete) == 1
    assert not any(event.data.get("name") == "a13n.service.run_recovery" for event in suffix)
    native = await live.events(run["id"])
    assert_native_contract(native, run)
    native_recovery = [event for event in native if event.kind == "run.recovery"]
    assert len(native_recovery) == 1
    source = native_recovery[0]
    assert recovery.data["value"] == {
        "schema_version": "1",
        "event_id": source.data["event_id"],
        "runId": hosted.body["runId"],
        "reason": "lease_expired",
    }
    index = native.index(source)
    assert await live.events(run["id"], after=native[index - 1].cursor) == native[index:]
    assert await live.events(run["id"], after=source.cursor) == native[index + 1 :]
    assert (await live.evidence(case))["effects"] == 1
    assert len(await lab.attempts(run["id"])) == 2
    logging.getLogger(__name__).info(
        "Recovery contract verified: run=%s source_event=%s hosted_cursor=%s",
        run["id"],
        source.data["event_id"],
        recovery.cursor,
    )
