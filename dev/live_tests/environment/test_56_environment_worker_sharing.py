"""Cross-process sharing, independent scopes, and exclusive Session contention."""

import asyncio
import os
import signal
from pathlib import Path

import pytest

from ..infrastructure.management_support import last_tool_result
from ..infrastructure.round_two_lab import REPOSITORY, open_lab
from .environment_backends import EnvironmentBackend
from .environment_workers import add_second_worker, native_file, reset_workers, shell

pytestmark = pytest.mark.anyio
KINDS = ["direct_local", "docker", "e2b", "http_envd", "websocket_envd"]


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module", params=KINDS)
async def worker_backend(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for two real Environment Workers")
    settings = None
    if request.param == "e2b":
        from ..providers.provider_config import load_provider_settings

        settings = load_provider_settings().environment
        if settings is None:
            pytest.skip("Configure E2B for its real two-Worker cases")
    binary = Path(os.environ.get("A13N_ENVD_TEST_BINARY", REPOSITORY / "target/debug/a13n-envd")).resolve()
    async with open_lab(
        suite="management", environment_workers=True, websocket_envd=request.param == "websocket_envd"
    ) as lab:
        lab.worker_environment["A13N_ENVD_EXECUTABLE"] = str(binary)
        yield EnvironmentBackend(lab, request.param, binary, settings)


@pytest.fixture
async def shared(worker_backend):
    backend = worker_backend
    await reset_workers(backend.lab)
    async with backend.target() as target:
        pair = await add_second_worker(backend.lab, no_reverse=backend.kind == "websocket_envd")
        try:
            yield backend, target, pair
        finally:
            pair.release_all()


@pytest.mark.parametrize("preparation", ["on_run", "on_use"])
async def test_first_prepare_is_serialized_between_processes(shared, preparation):
    backend, target, pair = shared
    if backend.kind in {"http_envd", "websocket_envd"}:
        pytest.skip("Registered external targets have no managed first-create operation")
    environment = await target.allocate(preparation=preparation)
    first, second = pair.workers
    barrier = pair.arm("environment.before_effect", environment_id=environment["id"], pid=first.pid)
    cases = [await pair.journey.case(steps=[shell(f"printf {index} > first-{index}")]) for index in range(2)]
    owner = await pair.start(first, cases[0], environment)
    await pair.reached(barrier)
    original = await pair.record(environment)
    assert original["state"] is None and original["generation"] == 0
    contender = await pair.start(second, cases[1], environment)
    # The second process holds a real Attempt but cannot publish or run tools.
    await asyncio.sleep(1)
    pending = await pair.record(environment)
    assert pending["operation_id"] == original["operation_id"]
    assert pending["generation"] == 0 and not (target.root / "first-1").exists()
    pair.release(barrier)
    await pair.journey.live.finish(owner["run_id"])
    await pair.journey.live.finish(contender["run_id"])
    assert await native_file(backend, target, environment, "first-1") == b"1"
    assert await native_file(backend, target, environment, "first-0") == b"0"
    row = await pair.record(environment)
    assert row["generation"] == 1 and row["operation_id"] is None
    _, result = await pair.execute(second, environment, [shell("printf retry > retry")])
    assert result["ok"] is True
    assert (await pair.record(environment))["state"] == row["state"]


@pytest.mark.parametrize("ending", ["cancel", "crash"])
async def test_one_user_lost_does_not_close_other_worker_use(shared, ending):
    backend, target, pair = shared
    if backend.kind not in {"direct_local", "docker", "e2b"}:
        pytest.skip("This provider admits one concurrent Session; contention is covered separately")
    environment = await target.allocate()
    cases = [
        await pair.journey.case(
            gate_at=1,
            steps=[shell(f"printf START > user-{index}"), shell(f"printf END >> user-{index}")],
        )
        for index in range(2)
    ]
    receipts = []
    for worker, case in zip(pair.workers, cases, strict=True):
        receipt = await pair.start(worker, case, environment)
        await pair.journey.ready(case, receipt["run_id"])
        receipts.append(receipt)
    before = await pair.record(environment)
    assert set(before["active_runs"]) == {receipt["run_id"] for receipt in receipts}
    if ending == "crash":
        await pair.lab.stop(pair.workers[0], signal.SIGKILL)
    await pair.journey.live.interrupt(receipts[0]["run_id"])
    await pair.journey.live.finish(receipts[0]["run_id"], "cancelled")
    assert (await pair.record(environment))["active_runs"] == [receipts[1]["run_id"]]
    assert await native_file(backend, target, environment, "user-0") == b"START"
    await pair.journey.live.release(cases[1])
    await pair.journey.live.finish(receipts[1]["run_id"])
    assert await native_file(backend, target, environment, "user-1") == b"STARTEND"
    after = await pair.record(environment)
    assert after["state"] == before["state"] and after["generation"] == 1
    assert after["active_runs"] == []


async def test_repeated_worker_handoffs_release_resources_and_preserve_shared_writes(shared):
    backend, target, pair = shared
    environment = await target.allocate()
    original = None
    for index in range(6):
        worker = pair.workers[0 if backend.kind == "websocket_envd" else index % 2]
        if index == 1 and backend.kind == "websocket_envd":
            # A process-local rendezvous is deliberately not a cross-Worker relay.
            case = await pair.journey.case(steps=[shell("printf STOLEN >> rounds")])
            receipt = await pair.start(pair.workers[1], case, environment)
            failure = await pair.journey.live.finish(receipt["run_id"], "failed")
            assert failure["failure"]["code"] == "attempt_execution_failed"
            assert await native_file(backend, target, environment, "rounds") == b"0"
        _, result = await pair.execute(worker, environment, [shell(f"printf {index} >> rounds; cat rounds")])
        assert result["ok"] is True
        row = await pair.record(environment)
        original = row if original is None else original
        assert row["state"] == original["state"]
        assert row["active_runs"] == [] and row["operation_id"] is None and row["generation"] == 1
        assert await native_file(backend, target, environment, "rounds") == "".join(map(str, range(index + 1))).encode()


async def test_concurrent_workers_preserve_owner_and_release_only_their_scope(shared):
    backend, target, pair = shared
    environment = await target.allocate(preparation="on_use")
    first, second = pair.workers
    owner_case = await pair.journey.case(
        gate_at=1,
        steps=[shell("printf OWNER > owner"), shell("cat owner; printf SURVIVED >> owner")],
    )
    owner = await pair.start(first, owner_case, environment)
    await pair.journey.ready(owner_case, owner["run_id"])
    before = await pair.record(environment)
    contender_case = await pair.journey.case(steps=[shell("printf OTHER > other")])
    contender = await pair.start(second, contender_case, environment)
    if backend.kind in {"http_envd", "websocket_envd"}:
        failed = await pair.journey.live.finish(contender["run_id"], "failed")
        assert failed["failure"]["code"] == "attempt_execution_failed"
    else:
        await pair.journey.live.finish(contender["run_id"])
        result = last_tool_result(pair.journey.observations(contender_case)[-1])
        assert result["ok"] is True, result
        assert await native_file(backend, target, environment, "other") == b"OTHER"
    if backend.kind in {"http_envd", "websocket_envd"}:
        assert not (target.root / "other").exists()
    assert (await pair.record(environment))["active_runs"] == [owner["run_id"]]
    assert (await pair.journey.live.run(owner["run_id"]))["status"] == "running"
    await pair.journey.live.release(owner_case)
    await pair.journey.live.finish(owner["run_id"])
    assert await native_file(backend, target, environment, "owner") == b"OWNERSURVIVED"
    after = await pair.record(environment)
    assert after["generation"] == before["generation"]
    assert after["active_runs"] == []
