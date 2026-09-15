"""P1: real HTTP failures and dependency recovery without restarting the Worker."""

import anyio
import pytest

from ..control.test_38_run_control_faults import assert_steer_once
from ..harness_integration.test_27_connectivity_execution import connection, requests

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("status", ["429", "503"])
@pytest.mark.parametrize("failures", [2, 15])
@pytest.mark.parametrize("run_faults", [{"worker": {"lease_seconds": 60}}], indirect=True)
async def test_explicit_model_rejections_retry_boundedly(run_faults, status, failures):
    journey = run_faults
    case = await journey.case(failure=status, failures=failures)
    receipt = await journey.start(case)
    result, attempts = await journey.assert_settled(receipt, outcome="completed" if failures == 2 else "failed")
    # Three transport tries per request, then up to five Harness ModelAttempts
    # inside the same Service RunAttempt. Neither budget is unbounded.
    assert len(attempts) == 1 and len(journey.observations(case)) == (3 if failures == 2 else 15)
    if failures == 2:
        assert result["output_text"] == case["token"]
    else:
        assert result["failure"]["code"] == "model_recovery_exhausted" and result["output_text"] is None


@pytest.mark.parametrize("failure", ["truncated", "malformed", "timeout"])
@pytest.mark.parametrize("failures", [1, 5])
@pytest.mark.parametrize("run_faults", [{"worker": {"lease_seconds": 60}}], indirect=True)
async def test_model_stream_failure_recovers_within_budget_without_false_completion(run_faults, failure, failures):
    journey = run_faults
    if failure == "truncated":
        await journey.patch(
            journey.base + "/model-providers/" + journey.live.config["model_provider_id"],
            {
                "configuration": {
                    "base_url": journey.live.config["peer_url"] + "/__live__/model/v1",
                    "auth_mode": "bearer",
                }
            },
        )
    agent = await journey.agent(
        model={
            "model_key": "live-fixture",
            "settings": {"timeout": 3},
            "characteristics": {"context_window_tokens": 32768},
        }
    )
    case = await journey.case(failure=failure, failures=failures, delay_seconds=10)
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    result, attempts = await journey.assert_settled(receipt, outcome="completed" if failures == 1 else "failed")
    assert "PARTIAL_" not in (result["output_text"] or ""), result
    observations = journey.observations(case)
    assert len(attempts) == 1 and len(observations) == (2 if failures == 1 else 5)
    assert "previous model stream ended" in str(observations[1]["body"]["messages"])
    if failures == 1:
        assert result["output_text"] == case["token"]
    else:
        assert result["failure"]["code"] == "model_recovery_exhausted" and result["output_text"] is None
    following = await journey.start(await journey.case())
    await journey.live.finish(following["run_id"])


@pytest.mark.parametrize("failure", ["exception", "timeout"])
@pytest.mark.parametrize(
    "run_faults", [{"worker": {"lease_seconds": 60}, "policy": {"max_attempts": 2}}], indirect=True
)
async def test_real_plugin_failure_does_not_create_an_effect_or_success_result(run_faults, failure):
    journey = run_faults
    case = await journey.case(effect=True, tool_failure=failure)
    receipt = await journey.start(case)
    result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=0)
    assert result["failure"] and result["output_text"] is None
    expected = 2 if failure == "timeout" else 1
    assert len(attempts) == len(journey.observations(case)) == expected
    assert all(attempt["status"] == "failed" for attempt in attempts)
    assert (await journey.live.evidence(case))["effect_attempts"] == expected


@pytest.mark.parametrize("dependency", ["postgres", "objects"])
async def test_dependency_flaps_recover_in_original_worker_process(run_faults, dependency):
    journey, lab = run_faults, run_faults.lab
    owner = lab.workers[0]
    proxy = lab.proxies[dependency]
    # Exercise disconnect/reconnect twice on the same process, each at a real
    # durable boundary. Evidence proves the cut was traversed before repair.
    for index in range(2):
        case = await journey.case(effect=True)
        point = "outcome.verified" if dependency == "postgres" else "checkpoint.before"
        barrier = journey.arm(f"boundary-{index}", point, kind="completed")
        receipt = await journey.start(case)
        await journey.reached(barrier)
        rejected = proxy.rejected
        proxy.cut()
        journey.release(barrier)
        try:

            async def connections():
                return proxy.rejected

            await journey.live.wait(
                connections, lambda count, previous=rejected: count > previous, "actual dependency rejection"
            )
        finally:
            proxy.restore()
        result, _ = await journey.assert_settled(receipt, case=case, effects=1)
        assert case["token"] in result["output_text"]
        assert owner.returncode is None and lab.workers == [owner]
        if dependency == "postgres":
            # The completed candidate was already persisted before this cut.
            assert len(journey.observations(case)) == 2
        # An object cut before the completed checkpoint may replay the final
        # model request. Its already-checkpointed tool effect must still be one.


@pytest.mark.parametrize("action", ["steer", "interrupt"])
async def test_redis_outage_preserves_control_intent_without_worker_restart(run_faults, action):
    journey, lab = run_faults, run_faults.lab
    owner = lab.workers[0]
    case = await journey.case(effect=True, idempotent=True)
    barrier = journey.arm("active-tool", "tool.after_effect", case_id=case["case_id"])
    receipt = await journey.start(case)
    await journey.reached(barrier)
    proxy = lab.proxies["redis"]
    proxy.cut()
    try:
        if action == "steer":
            steer, token = await journey.steer(receipt["run_id"])
            rows = await journey.inbox(receipt["thread_id"])
            assert len(rows) == 1 and rows[0]["id"] == steer["steer_id"] and rows[0]["status"] == "pending"

            async def rejections():
                return proxy.rejected

            await journey.live.wait(rejections, lambda count: count > 0, "Redis outage reached by Worker")
            # Redis also owns live publication leases. Repair transport after
            # durable acceptance, then require delivery without a process restart.
            proxy.restore()
        else:
            assert await journey.live.interrupt(receipt["run_id"])
        journey.release(barrier)
        result, attempts = await journey.assert_settled(
            receipt, outcome="completed" if action == "steer" else "cancelled", case=case, effects=1
        )
        assert attempts and owner.returncode is None
        if action == "steer":
            assert result["output_text"] == "STEERS:" + token
            assert_steer_once(journey.observations(case), token)
        else:
            assert result["output_text"] is None
        # Interrupt can settle through SQL even while the relay stays cut.
        await anyio.sleep(0.2)
        assert proxy.rejected > 0, "Worker did not traverse the Redis fault relay"
        assert await journey.live.run(receipt["run_id"]) == result
    finally:
        proxy.restore()
    following = await journey.start(await journey.case())
    await journey.live.finish(following["run_id"])
    assert lab.workers == [owner]


@pytest.mark.parametrize("run_faults", [{"policy": {"max_attempts": 2}}], indirect=True)
@pytest.mark.parametrize("failure_source", ["injected", "tcp"])
async def test_sustained_object_outage_exhausts_budget_then_same_worker_recovers(run_faults, failure_source):
    journey, lab = run_faults, run_faults.lab
    owner = lab.workers[0]
    proxy = lab.proxies["objects"]
    case = await journey.case(effect=True)
    fault = None
    if failure_source == "injected":
        fault = journey.arm("all-reads", "state.read_before", action="unavailable", times=8)
    else:
        proxy.cut()
    try:
        receipt = await journey.start(case)
        result, attempts = await journey.assert_settled(receipt, outcome="failed", case=case, effects=0)
        assert result["failure"] and len(attempts) == 2
        assert all(attempt["harness_run_id"] is None for attempt in attempts)
        assert journey.observations(case) == []
        if fault is not None:
            await journey.reached(fault, hit=8)
        else:
            assert proxy.rejected > 0
        assert owner.returncode is None
    finally:
        if failure_source == "tcp":
            proxy.restore()
    following = await journey.start(await journey.case())
    await journey.live.finish(following["run_id"])
    assert lab.workers == [owner]


async def test_lost_database_renewal_stops_tool_and_same_worker_recovers(run_faults):
    journey, lab = run_faults, run_faults.lab
    owner = lab.workers[0]
    case = await journey.case(effect=True)
    barrier = journey.arm("lease-bound-tool", "tool.before_effect", case_id=case["case_id"])
    receipt = await journey.start(case)
    await journey.reached(barrier)
    proxy = lab.proxies["postgres"]
    proxy.cut()
    try:
        # The tool is deliberately unreleased. Its finished marker can only
        # arrive through cancellation; the pause's own timeout is 90 seconds.
        # The lab lease is 12 seconds, with 3 seconds for bounded cleanup.
        with anyio.fail_after(15):
            while not (barrier / "finished-1.json").exists():
                await anyio.sleep(0.05)
        assert not (barrier / "release").exists() and journey.effects(case) == []
        assert proxy.rejected > 0 and owner.returncode is None
        assert (await journey.live.run(receipt["run_id"]))["sealed_at"] is None
    finally:
        proxy.restore()
        journey.release(barrier)
    result, attempts = await journey.assert_settled(receipt, case=case, effects=1)
    assert len(attempts) == 2 and lab.workers == [owner]
    assert case["token"] in result["output_text"]


@pytest.mark.parametrize("failure", ["error", "lost_response"])
@pytest.mark.parametrize("run_faults", [{"worker": {"lease_seconds": 60}}], indirect=True)
async def test_mcp_error_or_unknown_effect_is_reported_without_automatic_replay(run_faults, failure):
    journey = run_faults
    resource = await connection(journey, "mcp")
    agent = await journey.agent(
        connection_tools=[{"connection_id": resource["id"], "tools": ["live_echo"], "permission": "allow"}]
    )
    case = await journey.case()
    value = f"LIVE_FAULT:{failure}:{case['case_id']}"
    journey.plan(case, steps=[{"tool": "live_echo", "arguments": {"value": value}}])
    receipt = await journey.start(case, agent_id=agent["agent"]["id"])
    result, attempts = await journey.assert_settled(receipt, outcome="failed")
    assert len(attempts) == 1 and result["output_text"] is None
    assert result["failure"]["code"] == "attempt_execution_failed"
    dispatches = [item for item in requests(journey, "mcp") if item["body"].get("method") == "tools/call"]
    assert len(dispatches) == 1
    effects = journey.lab.root / "workspace" / case["case_id"] / "mcp-effect"
    assert effects.exists() == (failure == "lost_response")
    if effects.exists():
        assert effects.read_text() == value
