"""Real Docker retention, concurrent use, publication races, and Worker death."""

import asyncio
from types import SimpleNamespace

import anyio
import docker
import pytest
from a13n_harness.providers.environment.docker.runtime import DockerSDKEngine
from docker.errors import NotFound

from ..infrastructure.round_two_lab import open_lab
from .e2b_support import eventually
from .lifecycle_cases import LifecycleCases
from .lifecycle_support import ServiceEnvironments, shell

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="module")
async def docker_lab(request):
    if not request.config.getoption("--live-environments"):
        pytest.skip("Opt in for real Docker and Service lifecycle boundaries")
    async with open_lab(suite="management", docker_lifecycle=True) as lab:
        await lab.stop(lab.workers[-1])
        lab.worker_environment["A13N_SERVICE_WORKER_CONCURRENCY"] = "2"
        await lab.start_worker()
        yield lab


class DockerTargets:
    def __init__(self):
        self.engine = DockerSDKEngine(docker.from_env(timeout=30))
        self.identities = set()
        self.container_ids = set()

    async def targets(self, identity):
        assert identity in self.identities
        containers = await asyncio.to_thread(
            self.engine.client.containers.list, all=True, filters={"label": ["a13n.environment=" + identity]}
        )
        return [SimpleNamespace(container_id=c.id, status=c.status) for c in containers]

    async def info(self, target):
        assert target in self.container_ids
        try:
            container = await asyncio.to_thread(self.engine.client.containers.get, target)
        except NotFound:
            return None
        return SimpleNamespace(container_id=container.id, status=container.status)

    async def remove(self, target):
        container = await asyncio.to_thread(self.engine.client.containers.get, target)
        await asyncio.to_thread(container.remove, force=True)

    async def state(self, target, status):
        return await eventually(
            lambda: self.info(target),
            lambda info: ("absent" if info is None else info.status) == status,
            "Native Docker " + status,
        )


class ServiceContainers(ServiceEnvironments):
    provider = "docker"
    native_key = "container_id"
    stopped_status = "exited"

    async def allocate(self, *, stop_after=None, delete_after=None, preparation="on_run"):
        template, _, _ = await self.journey.environment_template(
            provider_type="docker",
            preparation=preparation,
            retention={"idle": {"stop_after": stop_after, "delete_after": delete_after}},
        )
        environment = await self.journey.post(self.journey.base + "/environments", {"template_id": template["id"]})
        self.environments.append(environment["id"])
        self.pool.identities.add(environment["id"])
        return environment

    async def target(self, environment):
        record = await self.record(environment)
        target = record["state"]["state"]["container_id"]
        self.track_target(target)
        return target

    def track_target(self, target):
        self.pool.container_ids.add(target)

    async def native_status(self, target):
        info = await self.pool.info(target)
        return "absent" if info is None else info.status

    async def native_targets(self, environment):
        return [item.container_id for item in await self.pool.targets(environment["id"])]


class TestDockerLifecycle(LifecycleCases):
    async def test_service_concurrent_runs_prepare_one_native_target(self, service):
        journey = service.journey
        environment = await service.allocate(preparation="on_use")
        selection = {"environment_id": environment["id"]}
        owner_case = await journey.case(gate_at=1, steps=[shell("printf owner-before"), shell("cat shared-marker")])
        owner = await journey.start(owner_case, environment=selection)
        await journey.ready(owner_case, owner["run_id"])
        target = await service.target(environment)
        contender_case = await journey.case(steps=[shell("printf contender > shared-marker")])
        contender = await journey.start(contender_case, environment=selection)
        assert (await journey.live.finish(contender["run_id"]))["status"] == "completed"
        assert await service.native_targets(environment) == [target]
        await journey.live.release(owner_case)
        assert "contender" in (await journey.live.finish(owner["run_id"]))["output_text"]

    @pytest.fixture
    async def service(self, docker_lab):
        pool = DockerTargets()
        service = ServiceContainers(docker_lab, pool)
        try:
            yield service
        finally:
            with anyio.CancelScope(shield=True), anyio.fail_after(180):
                errors = []
                try:
                    await service.journey.live.cleanup()
                    for identity in service.environments:
                        try:
                            await service.delete(identity)
                        except Exception as error:
                            errors.append(error)
                finally:
                    # Exact, test-owned labels also cover an unpublished create.
                    for identity in pool.identities:
                        for target in await pool.targets(identity):
                            await pool.remove(target.container_id)
                        assert not await pool.targets(identity)
                    await pool.engine.close()
                if errors:
                    raise ExceptionGroup("Service Docker cleanup failed", errors)
