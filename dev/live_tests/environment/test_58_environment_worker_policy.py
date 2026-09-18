"""Cross-Worker capacity, current policy, aggregate use and scope isolation."""

import asyncio
from pathlib import Path

import pytest

from ..infrastructure.management_support import last_tool_result
from ..infrastructure.round_two_lab import REPOSITORY, open_lab
from .environment_backends import EnvironmentBackend
from .environment_workers import add_second_worker, reset_workers, shell

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def policy_lab(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real Worker policy and capacity tests")
    async with open_lab(suite="management", environment_workers=True) as lab:
        yield lab


@pytest.fixture
async def local_workers(policy_lab, request):
    lab = policy_lab
    options = getattr(request, "param", {})
    await reset_workers(lab)
    await lab.stop(lab.workers[-1])
    lab.worker_environment.update(
        A13N_SERVICE_WORKER_CONCURRENCY="2",
        A13N_SERVICE_ENVIRONMENT_MAX_ACTIVE_PER_WORKSPACE=str(options.get("active", 4)),
    )
    await lab.start_worker()
    pair = await add_second_worker(lab)
    backend = EnvironmentBackend(lab, "direct-local", Path(REPOSITORY / "target/debug/a13n-envd"))
    try:
        async with backend.target() as target:
            yield backend, target, pair
    finally:
        pair.release_all()


@pytest.mark.parametrize("local_workers", [{"active": 1}], indirect=True)
async def test_shared_users_count_one_slot_until_last_user_releases(local_workers):
    backend, target, pair = local_workers
    environment = await target.allocate()
    cases, receipts = [], []
    for index, worker in enumerate(pair.workers):
        case = await pair.journey.case(gate_at=1, steps=[shell(f"printf user{index}")])
        receipt = await pair.start(worker, case, environment)
        await pair.journey.ready(case, receipt["run_id"])
        cases.append(case)
        receipts.append(receipt)
    assert len((await pair.record(environment))["active_runs"]) == 2
    async with backend.target() as other:
        blocked = await other.allocate(preparation="on_use")
        for index in range(2):
            _, result = await pair.execute(pair.workers[0], blocked, [shell("printf admitted > admitted")])
            assert result["ok"] is False and result["error"]["code"] == "environment_capacity_exceeded"
            assert not (other.root / "admitted").exists()
            if index == 0:
                await pair.journey.live.release(cases[0])
                await pair.journey.live.finish(receipts[0]["run_id"])
                assert (await pair.record(environment))["active_runs"] == [receipts[1]["run_id"]]
        await pair.journey.live.release(cases[1])
        await pair.journey.live.finish(receipts[1]["run_id"])
        _, result = await pair.execute(pair.workers[0], blocked, [shell("printf admitted > admitted")])
        assert result["ok"] is True and (other.root / "admitted").read_text() == "admitted"


@pytest.mark.parametrize("local_workers", [{"active": 1}], indirect=True)
async def test_distinct_environments_race_for_last_active_slot(local_workers):
    backend, target, pair = local_workers
    first = await target.allocate(preparation="on_use")
    async with backend.target() as other:
        second = await other.allocate(preparation="on_use")
        environments = [first, second]
        cases = [await pair.journey.case(gate_at=0, steps=[shell("printf reserved > reserved")]) for _ in range(2)]
        receipts = []
        barriers = []
        for worker, case, environment in zip(pair.workers, cases, environments, strict=True):
            receipt = await pair.start(worker, case, environment)
            await pair.journey.ready(case, receipt["run_id"])
            receipts.append(receipt)
            barriers.append(pair.arm("environment.before_effect", environment_id=environment["id"]))
        await asyncio.gather(*(pair.journey.live.release(case) for case in cases))

        async def winner():
            return [barrier for barrier in barriers if (barrier / "hit-1.json").exists()]

        hits = await pair.journey.live.wait(winner, bool, "One Worker reserved the last active slot")
        await asyncio.sleep(1)
        assert len(await winner()) == 1
        loser = 1 - barriers.index(hits[0])
        await pair.journey.live.finish(receipts[loser]["run_id"])
        denied = last_tool_result(pair.journey.observations(cases[loser])[-1])
        assert denied["error"]["code"] == "environment_capacity_exceeded"
        pair.release(hits[0])
        await pair.journey.live.finish(receipts[1 - loser]["run_id"])
        assert sum((root / "reserved").exists() for root in [target.root, other.root]) == 1


async def test_approval_wait_does_not_start_idle_clock_while_other_worker_uses_target(local_workers):
    _, target, pair = local_workers
    environment = await target.allocate()
    active_case = await pair.journey.case(gate_at=1, steps=[shell("printf active")])
    active = await pair.start(pair.workers[1], active_case, environment)
    await pair.journey.ready(active_case, active["run_id"])
    case = await pair.journey.live.case("approval")
    agent = await pair.journey.agent(
        plugins=[
            {
                "instance_name": "approval",
                "plugin_key": "live.approval",
                "config": {"root": pair.journey.live.config["workspace_root"]},
            }
        ]
    )
    receipt = await pair.start(pair.workers[0], case, environment, agent_id=agent["agent"]["id"])
    waiting = await pair.journey.live.finish(receipt["run_id"], "waiting")
    assert waiting["wait_reason"] == "approval"
    row = await pair.record(environment)
    assert row["retention_condition"] == "active" and row["active_runs"] == [active["run_id"]]
    await pair.journey.live.release(active_case)
    await pair.journey.live.finish(active["run_id"])
    idle = await pair.record(environment)
    assert idle["retention_condition"] == "idle" and idle["active_runs"] == []
    await asyncio.sleep(2)
    assert (await pair.record(environment))["condition_since"] == idle["condition_since"]


async def test_provider_disable_is_rechecked_for_both_accepted_users(local_workers):
    _, target, pair = local_workers
    environment = await target.allocate(preparation="on_use")
    cases, receipts = [], []
    for index, worker in enumerate(pair.workers):
        case = await pair.journey.case(gate_at=0, steps=[shell(f"printf denied > denied-{index}")])
        receipt = await pair.start(worker, case, environment)
        await pair.journey.ready(case, receipt["run_id"])
        cases.append(case)
        receipts.append(receipt)
    path = f"/api/v1/environment-providers/{target.provider['id']}"
    await pair.journey.patch(path, {"enabled": False})
    try:
        await asyncio.gather(*(pair.journey.live.release(case) for case in cases))
        for receipt, case in zip(receipts, cases, strict=True):
            await pair.journey.live.finish(receipt["run_id"])
            assert last_tool_result(pair.journey.observations(case)[-1])["ok"] is False
        assert not list(target.root.glob("denied-*"))
        assert (await pair.record(environment))["generation"] == 0
    finally:
        await pair.journey.patch(path, {"enabled": True})
    for worker in pair.workers:
        _, result = await pair.execute(worker, environment, [shell("printf restored")])
        assert result["ok"] is True


async def test_other_worker_cannot_use_live_process_handle_or_stdin(local_workers):
    _, target, pair = local_workers
    environment = await target.allocate()
    case = await pair.journey.case(gate_at=1, steps=[shell("printf ready; sleep 60", wait=0.1)])
    receipt = await pair.start(pair.workers[0], case, environment)
    await pair.journey.ready(case, receipt["run_id"])
    observed = last_tool_result(pair.journey.observations(case)[-1])
    process_id = observed.get("process_id") or observed.get("data", {}).get("process_id")
    assert process_id
    for tool, arguments in [
        ("shell_info", {"process_id": process_id}),
        ("shell_input", {"process_id": process_id, "data": "forbidden"}),
        ("shell_wait", {"process_id": process_id, "timeout_seconds": 0}),
    ]:
        _, result = await pair.execute(pair.workers[1], environment, [{"tool": tool, "arguments": arguments}])
        assert result["ok"] is False and result["error"]["code"] == "environment_not_found"
    assert (await pair.journey.live.run(receipt["run_id"]))["status"] == "running"
    await pair.journey.live.interrupt(receipt["run_id"])
    await pair.journey.live.finish(receipt["run_id"], "cancelled")


async def test_slow_environment_does_not_block_other_worker_or_environment(local_workers):
    backend, target, pair = local_workers
    environment = await target.allocate()
    barrier = pair.arm("environment.before_effect", environment_id=environment["id"])
    case = await pair.journey.case(steps=[shell("printf slow")])
    receipt = await pair.start(pair.workers[0], case, environment)
    await pair.reached(barrier)
    async with backend.target() as other:
        separate = await other.allocate()
        async with asyncio.timeout(15):
            _, result = await pair.execute(pair.workers[1], separate, [shell("printf independent > independent")])
        assert result["ok"] is True and (other.root / "independent").read_text() == "independent"
        assert (await pair.record(environment))["operation_id"] is not None
        pair.release(barrier)
        await pair.journey.live.finish(receipt["run_id"])
