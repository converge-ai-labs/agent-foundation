"""Shared targets retain accepted choices and each principal's current authority."""

import asyncio
import secrets
from uuid import uuid4

import pytest

from ..infrastructure.round_two_lab import open_lab, private_json
from .environment_backends import EnvironmentBackend
from .environment_workers import add_second_worker, reset_workers, shell

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def authority_lab(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for multi-Worker accepted selection and authorization")
    async with open_lab(suite="management", environment_workers=True, run_faults={"identity_management": True}) as lab:
        yield lab


@pytest.fixture
async def authorities(authority_lab):
    lab = authority_lab
    await reset_workers(lab)
    pair = await add_second_worker(lab)
    backend = EnvironmentBackend(lab, "direct-local")
    try:
        async with backend.target() as target:
            yield backend, target, pair
    finally:
        pair.release_all()


@pytest.mark.parametrize("revocation", ["disable", "role"])
async def test_revoked_user_cannot_borrow_other_worker_authority(authorities, revocation):
    _, target, pair = authorities
    environment = await target.allocate(preparation="on_use")
    active_case = await pair.journey.case(
        gate_at=1, steps=[shell("printf before > administrator"), shell("printf after >> administrator")]
    )
    active = await pair.start(pair.workers[0], active_case, environment)
    await pair.journey.ready(active_case, active["run_id"])
    account = await pair.journey.post(
        pair.journey.base + "/service-accounts", {"name": "Shared user " + uuid4().hex, "role": "runner"}
    )
    token = secrets.token_urlsafe(32)
    private_json(pair.lab.root / "fault-principals.json", [{"id": account["id"], "token": token}])
    # The tenth request still owns the accepted IAM snapshot; request eleven
    # must refresh it before dispatching another model request or tool.
    case = await pair.journey.case(
        gate_at=9,
        steps=[shell(f"printf accepted > accepted-{index}") for index in range(10)]
        + [shell("printf revoked > revoked")],
    )
    async with pair.only(pair.workers[1]):
        receipt = await pair.journey.live.request(
            "POST",
            pair.journey.base + "/runs",
            expected=202,
            headers={"Authorization": "Bearer " + token, "Idempotency-Key": case["case_id"]},
            json={**pair.journey.live.start_body(case), "environment": {"environment_id": environment["id"]}},
        )
        pair.journey.live.track(receipt)
        await pair.assert_owner(pair.workers[1], receipt)
    await pair.journey.ready(case, receipt["run_id"])
    await pair.journey.live.request(
        "PATCH",
        f"/api/v1/service-accounts/{account['id']}",
        json={
            "expected_version": account["version"],
            "name": account["name"],
            "status": "disabled" if revocation == "disable" else "active",
            "role": "viewer" if revocation == "role" else "runner",
        },
    )
    await pair.journey.live.release(case)
    result = await pair.journey.live.finish(receipt["run_id"], "failed")
    assert result["failure"]["code"] == "attempt_authorization_denied"
    assert len(pair.journey.observations(case)) == 10
    assert all((target.root / f"accepted-{index}").read_text() == "accepted" for index in range(10))
    assert not (target.root / "revoked").exists()
    assert (await pair.record(environment))["active_runs"] == [active["run_id"]]
    await pair.journey.live.release(active_case)
    await pair.journey.live.finish(active["run_id"])
    assert (target.root / "administrator").read_text() == "beforeafter"


async def test_worker_handoff_keeps_historical_selection_after_thread_default_changes(authorities):
    backend, target, pair = authorities
    environment = await target.allocate()
    original, _ = await pair.execute(pair.workers[0], environment, [shell("printf original > original")])
    async with backend.target() as other:
        replacement = await other.allocate()
        thread = await pair.journey.live.thread(original["thread_id"])
        continuation_case = await pair.journey.case(steps=[shell("printf replacement > replacement")])
        async with pair.only(pair.workers[1]):
            continuation = await pair.journey.post(
                f"/api/v1/runs/{original['id']}/continue",
                {
                    "expected_thread_version": thread["version"],
                    "input": pair.journey.live.start_body(continuation_case)["input"],
                    "environment": {"environment_id": replacement["id"]},
                },
                expected=202,
            )
            pair.journey.live.track(continuation)
            await pair.assert_owner(pair.workers[1], continuation)
        await pair.journey.live.finish(continuation["run_id"])
        assert (await pair.journey.live.thread(original["thread_id"]))["default_environment_id"] == replacement["id"]
        case = await pair.journey.case(
            steps=[shell("test -f original && test ! -f replacement && printf history > history")]
        )
        async with pair.only(pair.workers[1]):
            fork = await pair.journey.post(
                f"/api/v1/runs/{original['id']}/fork",
                {"input": pair.journey.live.start_body(case)["input"]},
                expected=202,
            )
            pair.journey.live.track(fork)
            await pair.assert_owner(pair.workers[1], fork)
        result = await pair.journey.live.finish(fork["run_id"])
        assert result["environment_id"] == environment["id"]
        assert (target.root / "history").read_text() == "history" and not (other.root / "history").exists()
        assert (await pair.journey.live.run(original["id"]))["environment_id"] == environment["id"]


async def test_shared_path_conflicts_follow_native_rename_semantics(authorities):
    _, target, pair = authorities
    environment = await target.allocate()
    cases, receipts = [], []
    for index, worker in enumerate(pair.workers):
        # Each writer prepares its own complete file before the publication race.
        case = await pair.journey.case(
            gate_at=1,
            steps=[
                shell(f"dd if=/dev/zero bs=32768 count=1 2>/dev/null | tr '\\000' {index} > writer-{index}"),
                shell(f"mv writer-{index} contested"),
            ],
        )
        receipt = await pair.start(worker, case, environment)
        await pair.journey.ready(case, receipt["run_id"])
        cases.append(case)
        receipts.append(receipt)
    await asyncio.gather(*(pair.journey.live.release(case) for case in cases))
    await asyncio.gather(*(pair.journey.live.finish(receipt["run_id"]) for receipt in receipts))
    assert (target.root / "contested").read_bytes() in {b"0" * 32768, b"1" * 32768}
    assert not list(target.root.glob("writer-*"))
    assert (await pair.record(environment))["generation"] == 1
