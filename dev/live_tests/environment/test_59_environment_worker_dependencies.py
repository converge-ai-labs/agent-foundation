"""Independent Worker database links and current remote credentials."""

import asyncio
import secrets
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from ..infrastructure.round_two_lab import REPOSITORY, open_lab
from ..infrastructure.tcp_proxy import TCPProxy
from .environment_backends import EnvironmentBackend
from .environment_workers import WorkerPair, add_second_worker, reset_workers, shell

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def dependency_lab(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for two independent Worker dependency connections")
    async with open_lab(suite="management", environment_workers=True) as lab:
        yield lab


@pytest.mark.parametrize("outage", ["one_worker", "both_workers"])
async def test_database_partition_never_speculatively_replays_native_effect(dependency_lab, outage):
    lab = dependency_lab
    await reset_workers(lab)
    await lab.stop(lab.workers[-1])
    direct = lab.environment["A13N_SERVICE_DATABASE_URL"]
    parts = urlsplit(direct)
    credentials = parts.netloc.rsplit("@", 1)[0] + "@"
    async with (
        TCPProxy(parts.hostname, parts.port).listen() as first_proxy,
        TCPProxy(parts.hostname, parts.port).listen() as second_proxy,
    ):
        workers = []
        for proxy in [first_proxy, second_proxy]:
            lab.worker_environment["A13N_SERVICE_DATABASE_URL"] = urlunsplit(
                parts._replace(netloc=credentials + f"127.0.0.1:{proxy.local_port}")
            )
            workers.append(await lab.start_worker())
        lab.worker_environment["A13N_SERVICE_DATABASE_URL"] = direct
        pair = WorkerPair(lab, workers)
        backend = EnvironmentBackend(lab, "direct_local")
        try:
            async with backend.target() as target:
                environment = await target.allocate()
                cases, receipts = [], []
                for index, worker in enumerate(workers):
                    case = await pair.journey.case(
                        gate_at=1,
                        steps=[shell(f"printf ONCE >> partition-{index}"), shell(f"printf AFTER >> partition-{index}")],
                    )
                    receipt = await pair.start(worker, case, environment)
                    await pair.journey.ready(case, receipt["run_id"])
                    cases.append(case)
                    receipts.append(receipt)
                before = await pair.record(environment)
                first_proxy.cut()
                if outage == "both_workers":
                    second_proxy.cut()
                # Native target remains accessible while only database connections fail.
                assert (target.root / "partition-0").read_text() == "ONCE"
                await pair.journey.live.release(cases[0])
                if outage == "one_worker":
                    async with asyncio.timeout(15):
                        await pair.journey.live.release(cases[1])
                        await pair.journey.live.finish(receipts[1]["run_id"])
                    assert (target.root / "partition-1").read_text() == "ONCEAFTER"
                else:
                    await pair.journey.live.release(cases[1])
                await asyncio.sleep(4)
                assert (target.root / "partition-0").read_text() == "ONCE"
                assert first_proxy.blocked and first_proxy.rejected > 0
                if outage == "both_workers":
                    assert (target.root / "partition-1").read_text() == "ONCE"
                    assert second_proxy.rejected > 0
                for receipt in receipts[: 1 if outage == "one_worker" else 2]:
                    await pair.journey.live.interrupt(receipt["run_id"])
                first_proxy.restore()
                second_proxy.restore()
                for receipt in receipts[: 1 if outage == "one_worker" else 2]:
                    await pair.journey.live.finish(receipt["run_id"], "cancelled")
                _, result = await pair.execute(workers[1], environment, [shell("printf restored > restored")])
                assert result["ok"] is True
                assert (target.root / "partition-0").read_text() == "ONCE"
                after = await pair.record(environment)
                assert after["state"] == before["state"] and after["generation"] == 1
        finally:
            first_proxy.restore()
            second_proxy.restore()
            pair.release_all()


async def test_remote_credential_rotation_is_seen_by_both_workers(dependency_lab):
    lab = dependency_lab
    await reset_workers(lab)
    binary = Path(REPOSITORY / "target/debug/a13n-envd")
    backend = EnvironmentBackend(lab, "http_envd", binary)
    async with backend.target() as target:
        pair = await add_second_worker(lab)
        environment = await target.allocate()
        _, result = await pair.execute(pair.workers[0], environment, [shell("printf initial > credential")])
        assert result["ok"] is True
        before = await pair.record(environment)
        replacement = secrets.token_urlsafe(32)
        (target.root.parent / "token").write_text(replacement)
        await target.restart_daemon()
        case = await pair.journey.case(steps=[shell("printf stale >> credential")])
        denied = await pair.start(pair.workers[1], case, environment)
        await pair.journey.live.finish(denied["run_id"], "failed")
        assert (target.root / "credential").read_text() == "initial"
        path = f"/api/v1/environment-providers/{target.provider['id']}"
        response = await pair.journey.live.http.get(path)
        assert response.status_code == 200
        await pair.journey.live.request(
            "PUT",
            path + "/credential",
            headers={"If-Match": response.headers["etag"]},
            json={"credential": {"token": replacement}},
        )
        for worker in reversed(pair.workers):
            _, result = await pair.execute(worker, environment, [shell("printf current >> credential")])
            assert result["ok"] is True
        assert (target.root / "credential").read_text() == "initialcurrentcurrent"
        after = await pair.record(environment)
        assert after["state"] == before["state"] and after["generation"] == 1
