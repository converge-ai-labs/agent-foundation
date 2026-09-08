"""Case 10: kill or suspend the current owner after a complete tool checkpoint."""

import signal

import pytest

from .stream import assert_stream

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("fault", ["crash", "stale_owner"])
async def test_worker_replacement_preserves_run_and_checkpoint(round_two, fault):
    lab, live = round_two, round_two.client
    case = await live.case("checkpoint")
    receipt = await live.start(case)
    run_id = receipt["run_id"]
    # The next model request contains the tool result and starts only after the post-tool checkpoint.
    await lab.wait_evidence(case, "checkpoint_ready", run_id=run_id)
    before = await live.run(run_id)
    first = (await lab.attempts(run_id))[0]
    assert (await live.evidence(case))["effects"] == 1
    owner = lab.workers[0]
    if fault == "crash":
        await lab.stop(owner, signal.SIGKILL)
    else:
        lab.send(owner, signal.SIGSTOP)
    await lab.start_worker()
    await live.wait(lambda: lab.attempts(run_id), lambda items: len(items) == 2, "replacement Attempt")
    await live.wait(lambda: live.evidence(case), lambda item: item["checkpoint_requests"] >= 2, "recovered tool result")
    replaced = (await lab.attempts(run_id))[0]
    await live.release(case)
    if fault == "stale_owner":
        lab.send(owner, signal.SIGCONT)
    finished = await live.finish(run_id)
    attempts = await lab.attempts(run_id)
    assert len(attempts) == 2
    assert attempts[0] == replaced and replaced["status"] == "failed"
    assert attempts[1]["status"] == "succeeded"
    assert attempts[1]["replaces_run_attempt_id"] == first["id"]
    assert attempts[1]["attempt_number"] > first["attempt_number"] and attempts[1]["fence"] > first["fence"]
    assert attempts[1]["harness_run_id"] != first["harness_run_id"]
    for key in ("id", "session_id", "thread_id", "input", "agent_revision_id", "parent_run_id"):
        assert finished[key] == before[key]
    assert finished["output_text"] == case["token"]
    assert (await live.evidence(case))["effects"] == 1, "A checkpointed tool effect was repeated"
    if fault == "stale_owner":
        await lab.stop(owner)
        assert await live.run(run_id) == finished, "Late old-owner work changed the seal"
        assert (await lab.attempts(run_id))[0] == replaced
    assert_stream(await live.events(run_id), run_id)
