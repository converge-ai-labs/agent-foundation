"""Real Worker composition with deterministic model and plugin dependency boundaries."""

from contextlib import AsyncExitStack, asynccontextmanager
from unittest.mock import AsyncMock, Mock

from a13n_environment_provider import EnvironmentProviderCatalog
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from a13n_service.models.provider_runtime import LiveProviderResolver
from a13n_service.plugins.on_demand import OnDemandPluginRuntime, PreparedOnDemandPluginRuntime
from a13n_service.process.resources import ExecutionResources
from a13n_service.process.runtime import SharedRuntime
from a13n_service.process.worker import build_worker_runtime
from a13n_service.secrets import SecretProtector
from a13n_service.storage.runtime import StorageResources
from anyio import CapacityLimiter
from fakeredis.aioredis import FakeRedis

from tests.lifecycle_support import test_lifecycle_writer


@asynccontextmanager
async def worker_runtime(sessions, objects, path, monkeypatch, *, settings, lock, model_factory, connectors=None):
    preflight = AsyncMock(return_value=PreparedOnDemandPluginRuntime(lock.digest, HarnessPluginFactoryCatalog(())))
    monkeypatch.setattr(OnDemandPluginRuntime, "prepare_for_claim", preflight)
    resources = Mock(spec=ExecutionResources)
    resources.native_model_factory = model_factory
    resources.live_model_providers = Mock(spec=LiveProviderResolver)
    resources.live_model_providers.resolve = AsyncMock(return_value=Mock())
    resources.plugin_objects = Mock()
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
            settings, shared, resources, EnvironmentProviderCatalog(), stack, connectors
        )
        assert runtime.execution_loop is not None
        assert any(component.run == runtime.execution_loop.run for component in background)
        yield runtime, shared, preflight
