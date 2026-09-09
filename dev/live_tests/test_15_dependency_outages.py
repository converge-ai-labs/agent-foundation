"""Case 15: cut Worker-only PostgreSQL, Redis or object-store TCP connections."""

import signal

import pytest

from .stream import assert_stream

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("dependency", ["postgres", "redis", "objects"])
async def test_dependency_outage_has_no_false_success_or_partial_seal(round_two, dependency):
    lab, live = round_two, round_two.client
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    await lab.wait_evidence(case, "checkpoint_ready", run_id=run_id)
    proxy = lab.proxies[dependency]
    proxy.cut()
    try:
        await live.release(case)

        async def rejected():
            return {"count": proxy.rejected}

        await live.wait(rejected, lambda value: value["count"] > 0, "Worker reached the disconnected dependency")
        observed = await live.run(run_id)  # Control retains its independent, healthy dependency connections.
        if dependency in {"postgres", "objects"}:
            assert observed["status"] != "completed", "Worker sealed success without its authoritative dependency"
        # Restart the fixture-owned Worker to exercise persisted recovery, even if it already exited.
        await lab.stop(lab.workers[0], signal.SIGKILL)
    finally:
        proxy.restore()
    await lab.start_worker()
    run = await live.wait(
        lambda: live.run(run_id), lambda value: value["status"] not in {"accepted", "running"}, "outage recovery"
    )
    assert run["status"] in {"completed", "failed"} and run["sealed_at"]
    if run["status"] == "completed":
        assert run["output_text"] == case["token"] and run["sealed_state_digest_sha256"]
    else:
        assert run["failure"]["code"] and run["failure"]["message"]
    attempts = await lab.attempts(run_id)
    assert all(attempt["status"] in {"succeeded", "failed", "yielded", "cancelled"} for attempt in attempts)
    assert [item["attempt_number"] for item in attempts] == sorted({item["attempt_number"] for item in attempts})
    # Redis delivery can explicitly report lost live history; other corruption must still fail.
    try:
        events = await live.events(run_id)
    except AssertionError as error:
        if dependency != "redis" or str(error) != "Run Stream reported a replay gap":
            raise
    else:
        assert_stream(events, run_id, run["status"])
    probe = await live.start(await live.case("basic"))
    await live.finish(probe["run_id"])
    assert await live.run(run_id) == run
