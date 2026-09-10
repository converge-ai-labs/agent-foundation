"""Two real lifecycle maintainers, native effects, takeover and stale owners."""

import asyncio
import json
import signal
from datetime import datetime, timedelta

import anyio
import pytest

from .e2b_support import E2BSandboxes, eventually, past
from .environment_workers import add_second_worker, reset_workers, shell
from .lifecycle_support import lifecycle_barrier
from .round_two_lab import open_lab
from .test_34_e2b_service_lifecycle import ServiceSandboxes
from .test_51_docker_service_lifecycle import DockerTargets, ServiceContainers

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", params=["docker", "e2b"])
async def lifecycle_lab(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real two-Worker lifecycle races")
    settings = None
    if request.param == "e2b":
        from .provider_config import load_provider_settings

        settings = load_provider_settings().environment
        if settings is None:
            pytest.skip("Configure E2B for its real two-Worker lifecycle cases")
    async with open_lab(
        suite="management",
        environment_workers=True,
        docker_lifecycle=request.param == "docker",
        e2b_lifecycle=request.param == "e2b",
    ) as lab:
        yield lab, request.param, settings


@pytest.fixture
async def coordinated(lifecycle_lab):
    lab, kind, settings = lifecycle_lab
    await reset_workers(lab)
    pair = await add_second_worker(lab)
    pool = DockerTargets() if kind == "docker" else E2BSandboxes(settings)
    service = ServiceContainers(lab, pool) if kind == "docker" else ServiceSandboxes(lab, pool)
    try:
        yield service, pair
    finally:
        pair.release_all()
        for worker in pair.workers:
            if worker.returncode is None:
                lab.send(worker, signal.SIGCONT)
        with anyio.CancelScope(shield=True), anyio.fail_after(180):
            try:
                await service.journey.live.cleanup()
                for identity in service.environments:
                    await service.delete(identity)
            finally:
                if kind == "e2b":
                    await pool.cleanup()
                else:
                    for identity in pool.identities:
                        for target in await pool.targets(identity):
                            await pool.engine.stop_container(target.container_id, timeout_seconds=1)
                            await pool.engine.remove_container(target.container_id)
                        assert not await pool.targets(identity)
                    await pool.engine.close()


@pytest.mark.parametrize("action", ["stop", "delete"])
async def test_two_maintainers_and_new_use_serialize_retention(coordinated, action):
    service, pair = coordinated
    environment = await service.allocate(**{action + "_after": 5})
    await pair.execute(pair.workers[0], environment, [shell("printf initial")])
    original = await pair.record(environment)
    target = await service.target(environment)
    with lifecycle_barrier(pair.lab, environment, action, provider=service.provider) as root:
        await eventually(lambda: anyio.Path(root / "claimed").exists(), bool, "One maintainer completed native effect")
        pending = await pair.record(environment)
        events = [
            event for event in pair.events(environment, point="environment.acquired") if event["action"] == action
        ]
        assert len(events) == 1, events
        maintainer = next(worker for worker in pair.workers if worker.pid == events[0]["pid"])
        other = next(worker for worker in pair.workers if worker is not maintainer)
        case = await pair.journey.case(gate_at=0, steps=[shell("printf after-retention")])
        receipt = await pair.start(other, case, environment)
        await asyncio.sleep(2)
        assert (await pair.record(environment))["operation_id"] == pending["operation_id"]
        assert pair.journey.observations(case) == []
        await service.pool.state(target, service.stopped_status if action == "stop" else "absent")
        (root / "release").touch()
        await pair.journey.ready(case, receipt["run_id"])
        current = await pair.record(environment)
        successor = await service.target(environment)
        assert current["generation"] == original["generation"] + (action == "delete")
        assert (target == successor) is (action == "stop")
        assert await service.native_targets(environment) == [successor]
        await pair.journey.live.release(case)
        await pair.journey.live.finish(receipt["run_id"])


@pytest.mark.parametrize("boundary", ["before_effect", "native_created", "committed"])
async def test_worker_death_reconciles_exact_operation_without_duplicate_target(coordinated, boundary):
    service, pair = coordinated
    environment = await service.allocate()
    first, second = pair.workers
    point = "environment.before_effect" if boundary == "before_effect" else "environment.after_publication"
    barrier = None
    if boundary != "native_created":
        barrier = pair.arm(point, environment_id=environment["id"], pid=first.pid, action="pause")
    with lifecycle_barrier(pair.lab, environment, "prepare", provider=service.provider) as root:
        if boundary != "native_created":
            (root / "release").touch()
        case = await pair.journey.case(steps=[shell("printf SHOULD_NOT_REPLAY > stale-effect")])
        receipt = await pair.start(first, case, environment)
        if boundary == "native_created":
            await eventually(lambda: anyio.Path(root / "claimed").exists(), bool, "Native create before publication")
            native = json.loads((root / "claimed").read_text())["target_id"]
            service.track_target(native)
        else:
            await pair.reached(barrier)
            native = await service.target(environment) if boundary == "committed" else None
        snapshot = await pair.record(environment)
        assert (snapshot["operation_id"] is None) is (boundary == "committed")
        await pair.lab.stop(first, signal.SIGKILL)
        await pair.journey.live.interrupt(receipt["run_id"])
        (root / "release").touch()
        if barrier:
            pair.release(barrier)
        reconciled = await eventually(
            lambda: pair.record(environment),
            lambda row: row["operation_id"] is None,
            "Other Worker reconciled the abandoned operation",
        )
        if native:
            assert await service.native_targets(environment) == [native]
            assert reconciled["generation"] == 1
        else:
            assert await service.native_targets(environment) == []
        # A committed prepare may leave an abandoned exclusive Docker Session.
        # Stop/resume is an explicit dependency change before asking a new Run to use it.
        if boundary == "committed" and service.provider == "docker":
            await pair.command(environment, "stop")
        _, result = await pair.execute(second, environment, [shell("test ! -e stale-effect && printf successor")])
        assert result["ok"] is True
        current = await pair.record(environment)
        assert current["generation"] == 1
        assert len(await service.native_targets(environment)) == 1
        if native:
            assert await service.target(environment) == native


async def test_zombie_worker_cannot_publish_after_other_worker_takes_over(coordinated):
    service, pair = coordinated
    environment = await service.allocate()
    first, second = pair.workers
    with lifecycle_barrier(pair.lab, environment, "prepare", provider=service.provider) as root:
        case = await pair.journey.case(steps=[shell("printf stale > stale-worker")])
        receipt = await pair.start(first, case, environment)
        await eventually(lambda: anyio.Path(root / "claimed").exists(), bool, "Old Worker created native target")
        native = json.loads((root / "claimed").read_text())["target_id"]
        service.track_target(native)
        original = await pair.record(environment)
        pair.lab.send(first, signal.SIGSTOP)
        try:
            await pair.journey.live.interrupt(receipt["run_id"])
            repaired = await eventually(
                lambda: pair.record(environment),
                lambda row: row["operation_id"] is None and row["state"] is not None,
                "Live second Worker acquired expired lifecycle authority",
            )
            assert repaired["operation_generation"] > original["operation_generation"]
            assert await service.native_targets(environment) == [native]
            (root / "release").touch()
        finally:
            pair.lab.send(first, signal.SIGCONT)

        async def rejected():
            return [
                event
                for event in pair.events(environment, point="environment.publication_rejected")
                if event["pid"] == first.pid
            ]

        await eventually(rejected, bool, "Resurrected old Worker publication fenced")
        assert (await pair.record(environment))["state"] == repaired["state"]
        _, result = await pair.execute(second, environment, [shell("test ! -e stale-worker && printf fenced")])
        assert result["ok"] is True
        assert await service.native_targets(environment) == [native]


async def test_expired_stop_owner_cannot_stop_new_use_after_resurrection(coordinated):
    service, pair = coordinated
    environment = await service.allocate(stop_after=5)
    await pair.execute(pair.workers[0], environment, [shell("printf initial")])
    native = await service.target(environment)
    barrier = pair.arm("environment.before_effect", environment_id=environment["id"], match={"action": "stop"})
    hit = await pair.reached(barrier)
    old = next(worker for worker in pair.workers if worker.pid == hit["pid"])
    current = next(worker for worker in pair.workers if worker is not old)
    pair.lab.send(old, signal.SIGSTOP)
    try:
        await service.status(environment, "stopped")
        case = await pair.journey.case(gate_at=0, steps=[shell("printf still-alive")])
        receipt = await pair.start(current, case, environment)
        await pair.journey.ready(case, receipt["run_id"])
        row = await pair.record(environment)
        assert row["active_runs"] == [receipt["run_id"]]
        pair.release(barrier)
    finally:
        pair.lab.send(old, signal.SIGCONT)

    async def rejected():
        return [
            event
            for event in pair.events(environment, point="environment.publication_rejected")
            if event["pid"] == old.pid
        ]

    await eventually(rejected, bool, "Old stop lease was fenced")
    assert await service.native_status(native) == "running", "Expired owner stopped a target with a new active user"
    await pair.journey.live.release(case)
    await pair.journey.live.finish(receipt["run_id"])


async def test_two_maintainers_renew_one_shared_target_past_original_expiry(coordinated):
    service, pair = coordinated
    if service.provider != "e2b":
        pytest.skip("Docker targets have no provider TTL renewal")
    environment = await service.allocate(timeout=45)
    cases, receipts = [], []
    for worker in pair.workers:
        case = await pair.journey.case(gate_at=0, steps=[shell("printf renewed")])
        receipt = await pair.start(worker, case, environment)
        await pair.journey.ready(case, receipt["run_id"])
        cases.append(case)
        receipts.append(receipt)
    native = await service.target(environment)
    before = await service.pool.info(native)

    async def evidence():
        return await pair.record(environment), await service.pool.info(native)

    row, after = await eventually(
        evidence,
        lambda values: (
            values[1] is not None
            and values[1].end_at > before.end_at + timedelta(seconds=5)
            and values[0]["expires_at"] is not None
            and datetime.fromisoformat(values[0]["expires_at"]) > before.end_at
        ),
        "Two maintainers renewed the shared E2B target",
    )
    assert after.end_at > before.end_at and row["generation"] == 1
    renewals = [
        event for event in pair.events(environment, point="environment.acquired") if event["action"] == "keepalive"
    ]
    assert renewals and len({(event["operation_id"], event["fence"]) for event in renewals}) == len(renewals)
    await past(before.end_at + timedelta(seconds=1))
    await service.pool.state(native, "running")
    await asyncio.gather(*(pair.journey.live.release(case) for case in cases))
    await asyncio.gather(*(pair.journey.live.finish(receipt["run_id"]) for receipt in receipts))
    assert await service.native_targets(environment) == [native]


@pytest.mark.parametrize("action", ["stop", "delete"])
async def test_two_workers_simultaneously_resume_or_rebuild_one_target(coordinated, action):
    service, pair = coordinated
    environment = await service.allocate(preparation="on_use")
    await pair.execute(pair.workers[0], environment, [shell("printf original")])
    original = await pair.record(environment)
    native = await service.target(environment)
    await pair.command(environment, action)
    cases, receipts = [], []
    for worker in pair.workers:
        case = await pair.journey.case(gate_at=0, steps=[shell("printf resumed")])
        receipt = await pair.start(worker, case, environment)
        await pair.journey.ready(case, receipt["run_id"])
        cases.append(case)
        receipts.append(receipt)
    await asyncio.gather(*(pair.journey.live.release(case) for case in cases))
    await asyncio.gather(*(pair.journey.live.finish(receipt["run_id"]) for receipt in receipts))
    outcomes = [pair.journey.observations(case)[-1] for case in cases]
    from .management_support import last_tool_result

    results = [last_tool_result(observation) for observation in outcomes]
    assert any(result["ok"] for result in results)
    if service.provider == "e2b":
        assert all(result["ok"] for result in results)
    current = await pair.record(environment)
    successor = await service.target(environment)
    assert current["generation"] == original["generation"] + (action == "delete")
    assert (successor == native) is (action == "stop")
    assert await service.native_targets(environment) == [successor]


async def test_stopped_target_keeps_capacity_until_deleted_by_other_worker(coordinated):
    service, original_pair = coordinated
    lab = original_pair.lab
    previous = dict(lab.worker_environment)
    try:
        lab.worker_environment["A13N_SERVICE_ENVIRONMENT_MAX_TARGETS_PER_WORKSPACE"] = "1"
        await reset_workers(lab)
        pair = await add_second_worker(lab)
        first = await service.allocate()
        await pair.execute(pair.workers[0], first, [shell("printf allocated")])
        native = await service.target(first)
        await pair.command(first, "stop")
        await service.pool.state(native, service.stopped_status)
        second = await service.allocate(preparation="on_use")
        _, denied = await pair.execute(pair.workers[1], second, [shell("printf forbidden")])
        assert denied["ok"] is False and denied["error"]["code"] == "environment_capacity_exceeded"
        assert await service.native_targets(second) == []
        await pair.command(first, "delete")
        await service.pool.state(native, "absent")
        _, allowed = await pair.execute(pair.workers[1], second, [shell("printf capacity-released")])
        assert allowed["ok"] is True
        assert len(await service.native_targets(second)) == 1
    finally:
        lab.worker_environment = previous
