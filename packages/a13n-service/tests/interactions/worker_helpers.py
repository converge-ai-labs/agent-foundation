"""Real Worker composition with deterministic model and plugin dependency boundaries."""

from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import AsyncMock, Mock

from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog
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
from tests.memory.selection_support import ordinary_memory


async def prepare_permissions(sessions, run, context, *, agent_ids=frozenset()):
    from a13n_service.interactions.models import RunRecord
    from a13n_service.storage import short_session

    from .conftest import WORKSPACE_ID

    # Acceptance fixtures can return the input Run before Environment selection.
    # Production preparation receives the final persisted Run from the claim.
    async with short_session(sessions) as session:
        run = (await session.get(RunRecord, run.id)).to_resource()
    await context.authorization.initialize(
        sessions,
        principal=run.authority_principal,
        organization_id=run.organization_id,
        workspace_id=WORKSPACE_ID,
        root_agent_id=run.agent_id,
        agent_ids=agent_ids,
        run_id=context.run_id,
        run_attempt_id=context.run_attempt_id,
        environment_id=run.environment_id,
    )
    return context


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
    await prepare_permissions(sessions, run, context)
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
    observability=None,
    environment_catalog=None,
    configuration_resolver=None,
    redis=None,
):
    resources = Mock(spec=ExecutionResources)
    resources.native_model_factory = model_factory
    resources.live_model_providers = Mock(spec=LiveProviderResolver)
    resources.live_model_providers.resolve = AsyncMock(return_value=Mock())
    resources.skill_package_store = Mock()
    resources.model_provider_registry = Mock()
    resources.model_endpoint_policy = Mock()
    resources.model_http_client = Mock()
    async with AsyncExitStack() as stack:
        if redis is None:
            redis = await stack.enter_async_context(FakeRedis())
        shared = SharedRuntime(
            StorageResources(Mock(), sessions, redis, objects, path, CapacityLimiter(4)),
            test_lifecycle_writer(),
            SecretProtector(key=b"k" * 32, encryption_key_id="test"),
            memory_behaviors=ordinary_memory(sessions),
        )
        runtime, background = await build_worker_runtime(
            settings,
            shared,
            resources,
            environment_catalog if environment_catalog is not None else EnvironmentProviderCatalog(),
            stack,
            connectors,
            invocations=invocations
            if invocations is not None
            else build_agent_resources(Components(), shared, resources.model_provider_registry).invocations,
            plugin_catalog=plugin_catalog if plugin_catalog is not None else HarnessPluginFactoryCatalog(()),
            observability=observability,
            configuration_resolver=configuration_resolver,
        )
        assert runtime.execution_loop is not None
        assert any(component.run == runtime.execution_loop.run for component in background)
        yield runtime, shared
