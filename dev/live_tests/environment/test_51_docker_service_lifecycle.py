"""Real Docker retention, concurrent use, publication races, and Worker death."""

import json

import anyio
import pytest
from a13n_environment import DockerSDKEngine

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
        self.engine = DockerSDKEngine.from_env(timeout_seconds=30)
        self.identities = set()
        self.container_ids = set()

    async def targets(self, identity):
        assert identity in self.identities
        return await self.engine.find_containers(
            {"io.a13n.environment-provider": "a13n.docker", "io.a13n.environment-id": identity}
        )

    async def info(self, target):
        assert target in self.container_ids
        return await self.engine.inspect_container(target)

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
            provider_type="a13n.docker",
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
        # HTTP EIP has one admitted Session. Concurrent users must not take it over.
        journey = service.journey
        environment = await service.allocate(preparation="on_use")
        selection = {"environment_id": environment["id"]}
        owner_case = await journey.case(
            gate_at=1, steps=[shell("printf owner-before"), shell("test ! -e rejected-marker && printf owner-after")]
        )
        owner = await journey.start(owner_case, environment=selection)
        await journey.ready(owner_case, owner["run_id"])
        target = await service.target(environment)
        original = await service.record(environment)
        contender_case = await journey.case(steps=[shell("printf contender > rejected-marker")])
        contender = await journey.start(contender_case, environment=selection)
        result = await journey.live.finish(contender["run_id"])
        tool_result = json.loads(json.loads(result["output_text"]))
        assert tool_result["ok"] is False
        assert tool_result["error"]["code"] == "environment_provider_failure"
        assert tool_result["error"]["retry_hint"] == "dependency_change"
        assert await service.native_targets(environment) == [target]
        assert (await service.record(environment))["generation"] == original["generation"] == 1
        assert (await journey.live.run(owner["run_id"]))["status"] == "running"
        await service.pool.state(target, "running")
        await journey.live.release(owner_case)
        assert "owner-after" in (await journey.live.finish(owner["run_id"]))["output_text"]
        retry = await journey.start(
            await journey.case(steps=[shell("printf after-owner-close")]), environment=selection
        )
        assert "after-owner-close" in (await journey.live.finish(retry["run_id"]))["output_text"]
        assert await service.target(environment) == target

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
                            await pool.engine.stop_container(target.container_id, timeout_seconds=1)
                            await pool.engine.remove_container(target.container_id)
                        assert not await pool.targets(identity)
                    await pool.engine.close()
                if errors:
                    raise ExceptionGroup("Service Docker cleanup failed", errors)
