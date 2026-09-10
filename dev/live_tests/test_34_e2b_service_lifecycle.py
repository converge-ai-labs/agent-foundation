"""Service scheduling, concurrent ownership and crash recovery over real E2B."""

import logging
from datetime import datetime, timedelta

import anyio
import pytest

from .e2b_support import eventually, past
from .lifecycle_cases import LifecycleCases
from .lifecycle_support import ServiceEnvironments, shell
from .round_two_lab import open_lab

pytestmark = pytest.mark.anyio
logger = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def e2b_lab(e2b_settings):
    # e2b_settings gates collection before Docker or private configuration is used.
    async with open_lab(suite="management", e2b_lifecycle=True) as lab:
        await lab.stop(lab.workers[-1])
        lab.worker_environment["A13N_SERVICE_WORKER_CONCURRENCY"] = "2"
        await lab.start_worker()
        yield lab


class ServiceSandboxes(ServiceEnvironments):
    provider = "e2b"
    native_key = "sandbox_id"
    stopped_status = "paused"

    async def native_status(self, target):
        info = await self.pool.info(target)
        return "absent" if info is None else info.state.value

    async def native_targets(self, environment):
        return [item.sandbox_id for item in await self.pool.targets(environment["id"])]

    def track_target(self, target):
        self.pool.sandbox_ids.add(target)

    async def allocate(self, *, timeout=300, stop_after=None, delete_after=None, preparation="on_run"):
        journey = self.journey
        provider = await journey.post(
            journey.base + "/environment-providers",
            {
                "name": "E2B lifecycle " + str(len(self.lab.client.runs)),
                "type": "a13n.e2b",
                "configuration": {},
                "credential": {"api_key": self.pool.settings.api_key.get_secret_value()},
            },
        )
        template = await journey.post(
            journey.base + "/environment-templates",
            {
                "name": "E2B lifecycle " + provider["id"],
                "provider_id": provider["id"],
                "access": "full",
                "preparation": preparation,
                "configuration": {"template": self.pool.settings.template, "timeout_seconds": timeout},
                "retention": {"idle": {"stop_after": stop_after, "delete_after": delete_after}},
            },
        )
        environment = await journey.post(journey.base + "/environments", {"template_id": template["id"]})
        self.environments.append(environment["id"])
        self.pool.identities.add(environment["id"])
        return environment

    async def target(self, environment):
        record = await self.record(environment)
        assert record["state"] is not None
        target = record["state"]["state"]["sandbox_id"]
        self.pool.sandbox_ids.add(target)
        return target


@pytest.fixture
async def e2b_service(e2b_lab, e2b_sandboxes):
    service = ServiceSandboxes(e2b_lab, e2b_sandboxes)
    try:
        yield service
    finally:
        with anyio.CancelScope(shield=True), anyio.fail_after(180):
            await service.journey.live.cleanup()
            errors = []
            for identity in service.environments:
                try:
                    await service.delete(identity)
                except Exception as error:
                    errors.append(identity + ": " + type(error).__name__)
            assert not errors, "Service E2B cleanup failed: " + "; ".join(errors)


async def test_e2b_service_renews_active_run_past_native_expiry(e2b_service):
    service, journey = e2b_service, e2b_service.journey
    environment = await service.allocate(timeout=45)
    case = await journey.case(gate_at=0, steps=[shell("printf survived-original-expiry")])
    receipt = await journey.start(case, environment={"environment_id": environment["id"]})
    await journey.ready(case, receipt["run_id"])
    target = await service.target(environment)
    before = await service.pool.info(target)

    async def evidence():
        return await service.record(environment), await service.pool.info(target)

    row, after = await eventually(
        evidence,
        lambda pair: (
            pair[1] is not None
            and pair[1].end_at > before.end_at + timedelta(seconds=5)
            and pair[0]["expires_at"] is not None
            and datetime.fromisoformat(pair[0]["expires_at"]) > before.end_at
        ),
        "Service published actual E2B renewal",
    )
    assert row["generation"] == 1
    logger.info("E2B renewal sandbox=%s old_expiry=%s new_expiry=%s", target, before.end_at, after.end_at)
    await past(before.end_at + timedelta(seconds=1))
    assert (await journey.live.run(receipt["run_id"]))["status"] == "running"
    await service.pool.state(target, "running")
    await journey.live.release(case)
    result = await journey.live.finish(receipt["run_id"])
    assert "survived-original-expiry" in result["output_text"]
    assert await service.target(environment) == target


class TestE2BLifecycle(LifecycleCases):
    @pytest.fixture
    def service(self, e2b_service):
        return e2b_service
