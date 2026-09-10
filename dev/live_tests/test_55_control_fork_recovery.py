"""Fork progresses independently of execution I/O, state recovery, and planned yield."""

import asyncio
import signal

import pytest

from .control_support import assert_absent
from .fork_support import accepted_reply, assert_fork, assert_paused, completed_source, paused_command

pytestmark = pytest.mark.anyio


async def execution_boundary(journey, source, phase):
    case = await journey.case(effect=phase == "tool")
    if phase == "waiting":
        journey.plan(case, steps=[{"tool": "live_client", "arguments": {"prompt": "Wait for feedback"}}])
    elif phase == "failed":
        journey.plan(case, failure="401", failures=100)
    point, role, match = {
        "model": ("model.request", "control", {"case_id": case["case_id"], "request": 1}),
        "tool": ("tool.after_effect", "worker", {"case_id": case["case_id"]}),
        "checkpoint": ("checkpoint.before", "worker", {"kind": "completed"}),
        "completed": ("outcome.verified", "worker", {"kind": "completed"}),
        "waiting": ("outcome.verified", "worker", {"kind": "waiting"}),
        "failed": ("control.failure_prepared", "worker", {"retryable": False}),
    }[phase]
    barrier = journey.arm("source-boundary", point, role=role, **match)
    current = await journey.accept(*await journey.command(source, "continue", case=case))
    await journey.reached(barrier)
    assert (await journey.live.run(current["run_id"]))["status"] == "running"
    return case, current, barrier


@pytest.mark.parametrize("phase", ["model", "tool", "checkpoint", "completed", "waiting", "failed"])
@pytest.mark.parametrize("paused", ["fork", "worker"])
async def test_fork_and_execution_progress_before_the_peer_boundary_opens(control, phase, paused):
    journey, live = control, control.live
    source = await completed_source(journey)
    await journey.lab.start_worker()
    case, current, boundary = await execution_boundary(journey, source, phase)
    fork_case = await journey.case()
    command = await journey.command(source, "fork", case=fork_case)
    outcome = phase if phase in {"waiting", "failed"} else "completed"
    if paused == "fork":
        async with paused_command(journey, *command, parent_run_id=source["id"]) as (barrier, task):
            journey.release(boundary)
            await live.finish(current["run_id"], outcome)
            after = await live.thread(source["thread_id"])
            assert_paused(barrier, task)
            journey.release(barrier)
            fork = await accepted_reply(journey, task)
            await assert_fork(journey, source, fork, fork_case)
            assert await live.thread(source["thread_id"]) == after
    else:
        before = await live.thread(source["thread_id"])
        fork = await journey.accept(*command)
        # Checkpoint publication holds a Run-local coordinator gate. Prove
        # acceptance before its reconciliation deadline; execution is checked
        # after release. Model/tool and outcome boundaries prove both at once.
        if phase != "checkpoint":
            await assert_fork(journey, source, fork, fork_case)
        assert_paused(boundary)
        assert (await live.run(current["run_id"]))["status"] == "running"
        assert await live.thread(source["thread_id"]) == before
        journey.release(boundary)
        await live.finish(current["run_id"], outcome)
        if phase == "checkpoint":
            await assert_fork(journey, source, fork, fork_case)
    assert_absent(journey.observations(fork_case), [case["case_id"]])
    assert await live.run(source["id"]) == source
    assert len(await journey.thread_runs(source)) == 2


async def finish_recovery(journey, current, case):
    result, attempts = await journey.assert_settled(current, case=case, effects=1)
    assert len(attempts) == 2
    assert attempts[0]["status"] == "failed" and attempts[1]["start_reason"] == "lease_expired"
    assert (await journey.state(result["id"]))["fence"] == 2
    assert case["token"] in result["output_text"]
    return await journey.live.thread(current["thread_id"])


@pytest.mark.parametrize("stage", ["read", "claim_before", "claim_after"])
@pytest.mark.parametrize("paused", ["fork", "recovery"])
async def test_fork_and_replacement_attempt_recover_before_the_peer_is_released(control, stage, paused):
    journey, live, lab = control, control.live, control.lab
    source = await completed_source(journey)
    await lab.start_worker()
    case = await journey.case(effect=True, idempotent=True)
    tool = journey.arm("effect", "tool.after_effect", case_id=case["case_id"])
    current = await journey.accept(*await journey.command(source, "continue", case=case))
    await journey.reached(tool)
    owner = lab.execution_owner(current["run_id"])
    point = "state.read_before" if stage == "read" else "fork.writer_" + stage
    match = {"run_id": current["run_id"]}
    if stage != "read":
        match["fence"] = 2
    recovery = journey.arm("recovery", point, **match)
    fork_case = await journey.case()
    command = await journey.command(source, "fork", case=fork_case)

    async def replace_owner():
        await lab.stop(owner, signal.SIGKILL)
        journey.release(tool)
        await lab.start_worker()
        await journey.reached(recovery)
        attempts = await lab.attempts(current["run_id"])
        assert len(attempts) == 2 and attempts[1]["start_reason"] == "lease_expired"

    if paused == "fork":
        async with paused_command(journey, *command, parent_run_id=source["id"]) as (barrier, task):
            await replace_owner()
            journey.release(recovery)
            after = await finish_recovery(journey, current, case)
            assert_paused(barrier, task)
            journey.release(barrier)
            fork = await accepted_reply(journey, task)
            await assert_fork(journey, source, fork, fork_case)
            assert await live.thread(source["thread_id"]) == after
    else:
        await replace_owner()
        before = await live.thread(source["thread_id"])
        fork = await journey.accept(*command)
        # State admission deliberately bounds each read/claim request. Require
        # independent HTTP acceptance within that window, then settle both Runs.
        assert_paused(recovery)
        assert await live.thread(source["thread_id"]) == before
        journey.release(recovery)
        await finish_recovery(journey, current, case)
        await assert_fork(journey, source, fork, fork_case)
    assert_absent(journey.observations(fork_case), [case["case_id"]])
    assert len(await journey.thread_runs(source)) == 2


@pytest.mark.parametrize("paused", ["fork", "yield"])
async def test_fork_and_planned_handoff_progress_before_the_peer_is_released(control, paused):
    journey, live, lab = control, control.live, control.lab
    source = await completed_source(journey)
    await lab.stop(lab.workers[0])
    # Give the draining owner enough time to hold a test barrier while its peer
    # executes. These are ordinary Worker settings, set before process creation.
    lab.worker_environment.update(A13N_SERVICE_WORKER_DRAIN_SECONDS="60", A13N_SERVICE_WORKER_LEASE_SECONDS="60")
    await lab.start_worker()
    await lab.start_worker()
    case = await journey.case(effect=True)
    tool = journey.arm("effect", "tool.before_effect", case_id=case["case_id"])
    current = await journey.accept(*await journey.command(source, "continue", case=case))
    await journey.reached(tool)
    owner = lab.execution_owner(current["run_id"])
    yielding = journey.arm("yield", "fork.yield_before", run_id=current["run_id"])
    fork_case = await journey.case()
    command = await journey.command(source, "fork", case=fork_case)

    async def begin_handoff():
        owner.send_signal(signal.SIGTERM)
        await lab.wait_unready(owner)
        journey.release(tool)
        await journey.reached(yielding)

    if paused == "fork":
        async with paused_command(journey, *command, parent_run_id=source["id"]) as (barrier, task):
            await begin_handoff()
            journey.release(yielding)
            await journey.assert_settled(current, case=case, effects=1)
            after = await live.thread(source["thread_id"])
            assert_paused(barrier, task)
            journey.release(barrier)
            fork = await accepted_reply(journey, task)
            await assert_fork(journey, source, fork, fork_case)
            assert await live.thread(source["thread_id"]) == after
    else:
        await begin_handoff()
        before = await live.thread(source["thread_id"])
        fork = await journey.accept(*command)
        await assert_fork(journey, source, fork, fork_case)
        assert_paused(yielding)
        assert await live.thread(source["thread_id"]) == before
        journey.release(yielding)
        await journey.assert_settled(current, case=case, effects=1)
    async with asyncio.timeout(30):
        await owner.wait()
    attempts = await lab.attempts(current["run_id"])
    assert len(attempts) == 2 and attempts[0]["status"] == "yielded"
    assert attempts[0]["yield_reason"] == "service_drain" and attempts[1]["start_reason"] == "planned_handoff"
    assert (await journey.execution(current["run_id"]))["handoffs_completed"] == 1
    assert_absent(journey.observations(fork_case), [case["case_id"]])
