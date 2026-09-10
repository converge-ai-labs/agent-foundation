"""Fork acceptance and execution remain independent of source queue consumption."""

import pytest

from .control_support import assert_absent
from .fork_support import accepted_reply, assert_fork, assert_paused, completed_source, paused_command

pytestmark = pytest.mark.anyio


async def finish_fifo(journey, current, rows, cases):
    parent = current["id"]
    for row, case in zip(rows, cases, strict=True):
        result = await journey.finish_queue(row)
        assert result["parent_run_id"] == parent and result["output_text"] == case["token"]
        assert len(await journey.lab.attempts(result["id"])) == 1
        parent = result["id"]
    thread = await journey.live.thread(current["thread_id"])
    assert thread["current_run_id"] == thread["head_run_id"] == parent
    return thread


@pytest.mark.parametrize("mode", ["explicit", "handoff", "shared_handoff"])
@pytest.mark.parametrize("paused", ["fork", "queue"])
async def test_fork_and_queue_consumption_progress_before_the_peer_is_released(control, mode, paused):
    journey, live, lab = control, control.live, control.lab
    environment = None
    if mode == "shared_handoff":
        resource, _ = await journey.environment()
        environment = {"environment_id": resource["id"]}
    source = await completed_source(journey, environment=environment)
    # The default fault lab's short lease targets recovery tests. This matrix
    # deliberately pauses a Run-local completion gate while a peer executes.
    await lab.stop(lab.workers[0])
    lab.worker_environment["A13N_SERVICE_WORKER_LEASE_SECONDS"] = "60"
    await lab.start_worker()
    await lab.start_worker()
    case = await journey.case()
    model = journey.arm("source-model", "model.request", role="control", case_id=case["case_id"], request=1)
    current = await journey.accept(*await journey.command(source, "continue", case=case))
    await journey.reached(model)
    cases = [await journey.case(), await journey.case()]
    rows = [await journey.queue(current, following) for following in cases]
    fork_case = await journey.case()
    fork_command = await journey.command(source, "fork", case=fork_case)
    if mode == "explicit":
        recovery = journey.arm(
            "background-queue", "control.queue_recovery", role="control", times=100, thread_id=source["thread_id"]
        )
        await journey.post(*await journey.command(current, "interrupt"), expected=202)
        journey.release(model)
        await live.finish(current["run_id"], "cancelled")
        command = await journey.command(current, "consume")
        if paused == "fork":
            async with paused_command(journey, *fork_command, parent_run_id=source["id"]) as (barrier, task):
                first = await journey.accept(*command)
                assert (await journey.queue_row(rows[0]))["consumed_run_id"] == first["run_id"]
                journey.release(recovery)
                after = await finish_fifo(journey, source, rows, cases)
                assert_paused(barrier, task)
                journey.release(barrier)
                fork = await accepted_reply(journey, task)
                await assert_fork(journey, source, fork, fork_case)
                assert await live.thread(source["thread_id"]) == after
        else:
            async with paused_command(
                journey, *command, point="queue.consume_prepared", consumer="explicit", thread_id=source["thread_id"]
            ) as (barrier, task):
                before = await live.thread(source["thread_id"])
                queued = await journey.queued(source)
                fork = await journey.accept(*fork_command)
                await assert_fork(journey, source, fork, fork_case)
                assert_paused(barrier, task)
                assert await live.thread(source["thread_id"]) == before
                assert await journey.queued(source) == queued
                journey.release(barrier)
                first = await accepted_reply(journey, task)
                assert (await journey.queue_row(rows[0]))["consumed_run_id"] == first["run_id"]
                journey.release(recovery)
            await finish_fifo(journey, source, rows, cases)
        journey.release(recovery)
    else:
        handoff = journey.arm("handoff", "fork.handoff_prepared", run_id=current["run_id"])
        journey.release(model)
        hit = await journey.reached(handoff)
        if paused == "fork":
            async with paused_command(journey, *fork_command, parent_run_id=source["id"]) as (barrier, task):
                assert_paused(handoff)
                journey.release(handoff)
                completed = await live.finish(current["run_id"])
                after = await finish_fifo(journey, completed, rows, cases)
                assert_paused(barrier, task)
                journey.release(barrier)
                fork = await accepted_reply(journey, task)
                await assert_fork(journey, source, fork, fork_case)
                assert await live.thread(source["thread_id"]) == after
        else:
            before = await live.thread(source["thread_id"])
            queued = await journey.queued(source)
            fork = await journey.accept(*fork_command)
            await assert_fork(journey, source, fork, fork_case)
            assert_paused(handoff)
            assert await live.thread(source["thread_id"]) == before
            assert await journey.queued(source) == queued
            assert (await live.http.get(f"/api/v1/runs/{hit['successor_run_id']}")).status_code == 404
            journey.release(handoff)
            completed = await live.finish(current["run_id"])
            await finish_fifo(journey, completed, rows, cases)
        assert (await journey.queue_row(rows[0]))["consumed_run_id"] == hit["successor_run_id"]
    if environment:
        result = await live.run(fork["run_id"])
        assert result["environment_id"] == source["environment_id"]
    assert_absent(journey.observations(fork_case), [item["case_id"] for item in [case, *cases]])
    assert len(await journey.thread_runs(source)) == 4
    assert await live.run(source["id"]) == source
