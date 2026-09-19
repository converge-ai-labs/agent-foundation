"""Worker relay startup and reserved response-reader capacity."""

from contextlib import AsyncExitStack

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.environment import EnvironmentProviderDefinition
from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY

from a13n_service.environments.websocket.relay_capability import validate_relay_backend
from a13n_service.environments.websocket.worker_connections import WorkerClientConnections
from a13n_service.settings import Settings
from a13n_service.storage.config import RedisServerConfig
from a13n_service.storage.redis import open_redis

from .runtime import SharedRuntime


async def build_worker_client_connections(
    settings: Settings,
    shared: SharedRuntime,
    catalog: ProviderCatalog[EnvironmentProviderDefinition],
    stack: AsyncExitStack,
    instance_id: str,
) -> WorkerClientConnections | None:
    if WEBSOCKET_PROVIDER_KEY not in catalog:
        return None
    configuration = settings.redis_config()
    if not isinstance(configuration, RedisServerConfig):
        raise ValueError("Client WebSocket Environments require a shared Redis server")
    await validate_relay_backend(shared.storage.redis)
    reader = await stack.enter_async_context(open_redis(configuration.model_copy(update={"max_connections": 1})))
    connections = WorkerClientConnections(shared.storage.redis, reader, instance_id)
    await connections.prepare()
    stack.push_async_callback(connections.close)
    return connections
