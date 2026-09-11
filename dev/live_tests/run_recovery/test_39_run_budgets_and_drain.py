"""P0/P1: accepted execution budgets, persistent backoff and forced drain paths."""

import asyncio
import signal
from datetime import UTC, datetime

import anyio
import pytest

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("run_faults", [{"policy": {"max_attempts": 2}}], indirect=True)
async def test_repeated_worker_loss_exhausts_run_budget_without_third_attempt(run_faults):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    case = await journey.case()
    first = journey.arm("first", "model.request", role="control", case_id=case["case_id"], request=1)
    second = journey.arm("second", "model.request", role="control", case_id=case["case_id"], request=2)
    receipt = await journey.start(case)
    await journey.reached(first)
    await lab.stop(lab.workers[0], signal.SIGKILL)
    journey.release(first)
    owner = await lab.start_worker()
    await journey.reached(second)
    await lab.stop(owner, signal.SIGKILL)
    journey.release(second)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, outcome="failed")
    assert result["failure"]["code"] == "execution_attempts_exhausted"
    assert len(attempts) == (await journey.execution(result["id"]))["attempts_charged"] == 2
    assert all(attempt["status"] == "failed" for attempt in attempts)
    assert len(journey.observations(case)) == 2
    await live.assert_stable(lambda: lab.attempts(result["id"]), attempts, seconds=1)
    following = await journey.start(await journey.case())
    await live.finish(following["run_id"])


@pytest.mark.parametrize("run_faults", [{"policy": {"max_attempts": 0}}], indirect=True)
async def test_zero_budget_rejects_execution_without_model_or_attempt(run_faults):
    journey = run_faults
    case = await journey.case(effect=True)
    receipt = await journey.start(case)
    result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=0)
    assert result["failure"]["code"] == "execution_attempts_exhausted" and attempts == []
    assert journey.observations(case) == []


@pytest.mark.parametrize("run_faults", [{"policy": {"deadline_seconds": 4}}], indirect=True)
async def test_deadline_expires_while_accepted_without_reset_on_worker_start(run_faults):
    journey, lab = run_faults, run_faults.lab
    await lab.stop(lab.workers[0])
    case = await journey.case(effect=True)
    receipt = await journey.start(case)
    accepted = await journey.execution(receipt["run_id"])
    deadline = datetime.fromisoformat(accepted["execution_budget"]["execution_deadline_at"])
    await anyio.sleep(max(0, (deadline - datetime.now(UTC)).total_seconds()) + 0.1)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=0)
    assert result["failure"]["code"] == "execution_deadline_exhausted" and attempts == []
    assert (await journey.execution(result["id"]))["execution_budget"] == accepted["execution_budget"]
    assert journey.observations(case) == []


@pytest.mark.parametrize("run_faults", [{"policy": {"max_usage": {"model_requests": 1}}}], indirect=True)
async def test_recovery_preserves_already_charged_model_request_usage(run_faults):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    case = await journey.case()
    barrier = journey.arm("first-request", "model.request", role="control", case_id=case["case_id"], request=1)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    await lab.stop(lab.workers[0], signal.SIGKILL)
    journey.release(barrier)
    await lab.start_worker()
    result, attempts = await journey.assert_settled(receipt, outcome="failed")
    assert len(attempts) == 2
    assert (await journey.execution(result["id"]))["usage_charged"]["model_requests"] == 1
    assert len(journey.observations(case)) == 1, "Replacement reset the Run-owned usage ceiling"
    assert result["failure"] and result["output_text"] is None
    assert await live.run(result["id"]) == result


@pytest.mark.parametrize("run_faults", [{"retry_after_seconds": 10}], indirect=True)
async def test_preparation_retry_backoff_survives_worker_restart(run_faults):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    await lab.stop(lab.workers[0])
    case = await journey.case()
    receipt = await journey.start(case)
    fault = journey.arm("reads", "state.read_before", run_id=receipt["run_id"], action="unavailable", times=4)
    failed = journey.arm("failed", "attempt.failed", run_id=receipt["run_id"], fence=1)
    owner = await lab.start_worker()
    await journey.reached(fault, hit=4)
    await journey.reached(failed)
    backoff = await journey.execution(receipt["run_id"])
    assert (await live.run(receipt["run_id"]))["status"] == "running" and backoff["current_run_attempt_id"] is None
    available = datetime.fromisoformat(backoff["available_at"])
    assert available > datetime.now(UTC)
    await lab.stop(owner, signal.SIGKILL)
    journey.release(failed)
    await lab.start_worker()
    assert datetime.now(UTC) < available, "Fixture restart missed the backoff observation window"
    observed = await journey.execution(receipt["run_id"])
    assert observed["available_at"] == backoff["available_at"]
    assert len(await lab.attempts(receipt["run_id"])) == 1 and journey.observations(case) == []
    result, attempts = await journey.assert_settled(receipt)
    assert len(attempts) == 2 and attempts[1]["start_reason"] == "attempt_failed"
    assert datetime.fromisoformat(attempts[1]["created_at"]) >= available
    assert result["output_text"] == case["token"]


@pytest.mark.parametrize("fault", ["unavailable", "conflict"])
async def test_transient_writer_claim_fault_retries_inside_same_attempt(run_faults, fault):
    journey = run_faults
    barrier = journey.arm("claim", "state.put_before", kind="initial", fence=1, action=fault)
    case = await journey.case(effect=True)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
    assert len(attempts) == 1 and case["token"] in result["output_text"]
    assert len(journey.observations(case)) == 2


@pytest.mark.parametrize("run_faults", [{"worker": {"drain_seconds": 25}}], indirect=True)
async def test_sigterm_forces_planned_handoff_after_tool_checkpoint(run_faults):
    journey, lab = run_faults, run_faults.lab
    case = await journey.case(effect=True)
    tool = journey.arm("active-tool", "tool.before_effect", case_id=case["case_id"])
    receipt = await journey.start(case)
    await journey.reached(tool)
    owner = lab.workers[0]
    owner.send_signal(signal.SIGTERM)
    await lab.wait_unready(owner)
    await lab.start_worker()
    assert len(await lab.attempts(receipt["run_id"])) == 1
    journey.release(tool)
    async with asyncio.timeout(30):
        await owner.wait()
    first = (await lab.attempts(receipt["run_id"]))[0]
    assert first["status"] == "yielded" and first["yield_reason"] == "service_drain"
    result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
    assert len(attempts) == 2 and attempts[1]["start_reason"] == "planned_handoff"
    execution = await journey.execution(result["id"])
    assert execution["handoffs_completed"] == 1 and execution["attempts_charged"] == 1
    assert len(journey.observations(case)) == 2


async def test_drain_deadline_does_not_permit_takeover_before_lease_expiry(run_faults):
    journey, lab = run_faults, run_faults.lab
    case = await journey.case()
    barrier = journey.arm("model", "model.request", role="control", case_id=case["case_id"], request=1)
    receipt = await journey.start(case)
    await journey.reached(barrier)
    owner = lab.workers[0]
    owner.send_signal(signal.SIGTERM)
    await lab.wait_unready(owner)
    await lab.start_worker()
    async with asyncio.timeout(15):
        await owner.wait()
    first = (await journey.execution(receipt["run_id"]))["attempts"][0]
    expiry = datetime.fromisoformat(first["lease_expires_at"])
    assert datetime.now(UTC) < expiry, "Drain test must observe the unexpired lease"
    assert len(await lab.attempts(receipt["run_id"])) == 1
    journey.release(barrier)
    result, attempts = await journey.assert_settled(receipt)
    assert len(attempts) == 2 and attempts[1]["start_reason"] == "lease_expired"
    assert datetime.fromisoformat(attempts[1]["created_at"]) >= expiry
    assert result["output_text"] == case["token"]


async def test_slow_checkpoint_keeps_renewing_and_excludes_competing_worker(run_faults):
    journey, live, lab = run_faults, run_faults.live, run_faults.lab
    case = await journey.case(effect=True)
    barrier = journey.arm("slow-checkpoint", "checkpoint.before", kind="completed")
    receipt = await journey.start(case)
    await journey.reached(barrier)
    first = (await journey.execution(receipt["run_id"]))["attempts"][0]
    initial_expiry = datetime.fromisoformat(first["lease_expires_at"])
    await lab.start_worker()
    await anyio.sleep(max(0, (initial_expiry - datetime.now(UTC)).total_seconds()) + 0.2)
    current = (await journey.execution(receipt["run_id"]))["attempts"]
    assert len(current) == 1 and current[0]["id"] == first["id"]
    assert datetime.fromisoformat(current[0]["lease_expires_at"]) > initial_expiry
    assert (await live.run(receipt["run_id"]))["status"] == "running"
    journey.release(barrier)
    await journey.assert_settled(receipt, case=case, effects=1)
