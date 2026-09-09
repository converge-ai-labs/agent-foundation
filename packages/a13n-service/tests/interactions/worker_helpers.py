"""Real Worker composition with deterministic model and plugin dependency boundaries."""

from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import AsyncMock, Mock

from a13n_environment import EnvironmentProviderCatalog
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.process.agents import build_agent_resources
from a13n_service.process.components import Components
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.process.worker import build_worker_runtime
from a13n_service.secrets import SecretProtector
from a13n_service.storage.runtime import StorageResources
from anyio import CapacityLimiter
from fakeredis.aioredis import FakeRedis

from tests.lifecycle_support import test_lifecycle_writer


async def accepted_running_attempt(sessions, objects):
    from a13n_service.interactions.attempts import AttemptExecutionService
    from a13n_service.interactions.scheduling import AttemptScheduler, ClaimedAttempt

    from tests.hooks.support import seed_hook_actor_access

    from .conftest import NOW
    from .test_attempt_execution import _accept_root, _authority, _worker

    await seed_hook_actor_access(sessions)
    _, run, _ = await _accept_root(sessions, objects)
    claim = await AttemptScheduler(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer()).claim(
        run.id, _worker()
    )
    assert isinstance(claim, ClaimedAttempt)
    execution = AttemptExecutionService(sessions, clock=lambda: NOW, lifecycle=test_lifecycle_writer())
    context = _authority(claim)
    preparation = await execution.commit_preparation_success(context)
    await execution.enter_harness(context, preparation=preparation, harness_run_id="integration-test")
    return run, context


@asynccontextmanager
async def worker_runtime(
    sessions,
    objects,
    path,
    monkeypatch,
    *,
    settings,
    model_factory,
    connectors=None,
    plugin_catalog=None,
    invocations=None,
):
    resources = Mock(spec=ExecutionResources)
    resources.native_model_factory = model_factory
    resources.live_model_providers = Mock(spec=LiveProviderResolver)
    resources.live_model_providers.resolve = AsyncMock(return_value=Mock())
    resources.skill_package_store = Mock()
    resources.model_provider_registry = Mock()
    resources.model_endpoint_policy = Mock()
    resources.model_http_client = Mock()
    async with FakeRedis() as redis, AsyncExitStack() as stack:
        shared = SharedRuntime(
            StorageResources(Mock(), sessions, redis, objects, path, CapacityLimiter(4)),
            test_lifecycle_writer(),
            SecretProtector(key=b"k" * 32, encryption_key_id="test"),
        )
        runtime, background = await build_worker_runtime(
            settings,
            shared,
            resources,
            EnvironmentProviderCatalog(),
            stack,
            connectors,
            invocations=invocations
            if invocations is not None
            else build_agent_resources(Components(), shared, resources.model_provider_registry).invocations,
            plugin_catalog=plugin_catalog if plugin_catalog is not None else HarnessPluginFactoryCatalog(()),
        )
        assert runtime.execution_loop is not None
        assert any(component.run == runtime.execution_loop.run for component in background)
        yield runtime, shared
