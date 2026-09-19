"""Shared relay startup and reserved response-reader capacity."""

from contextlib import AsyncExitStack

from a13n_environment import EnvironmentProviderCatalog
from a13n_environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY

from a13n_service.environments.websocket.relay_capability import validate_relay_backend
from a13n_service.environments.websocket.relay_runtime import RelayResponseRuntime
from a13n_service.ids import new_object_id
from a13n_service.settings import Settings
from a13n_service.storage import StorageResources
from a13n_service.storage.config import RedisServerConfig
from a13n_service.storage.redis import open_redis


async def build_relay_responses(
    settings: Settings,
    storage: StorageResources,
    catalog: EnvironmentProviderCatalog,
    stack: AsyncExitStack,
) -> RelayResponseRuntime | None:
    if WEBSOCKET_PROVIDER_KEY not in catalog:
        return None
    configuration = settings.redis_config()
    if not isinstance(configuration, RedisServerConfig):
        raise ValueError("Client WebSocket Environments require a shared Redis server")
    await validate_relay_backend(storage.redis)
    reader = await stack.enter_async_context(open_redis(configuration.model_copy(update={"max_connections": 1})))
    responses = RelayResponseRuntime(storage.redis, reader, new_object_id("svc"))
    await responses.prepare()
    stack.push_async_callback(responses.close)
    return responses
