"""Compose Environment authoring and OSS deployment-owned local Providers."""

from contextlib import AsyncExitStack

from a13n_harness.providers.environment.catalog import EnvironmentProviderCatalog
from a13n_harness.providers.environment.remote_envd.connections import WEBSOCKET_PROVIDER_KEY

from a13n_service.environments.domain import LOCAL_PROVIDER_TYPES
from a13n_service.environments.local import synchronize_local_providers
from a13n_service.environments.service import EnvironmentService
from a13n_service.environments.websocket.connection_host import ClientConnectionHost
from a13n_service.environments.websocket.coordination import ConnectionCoordination
from a13n_service.environments.websocket.reconciliation import ClientConnectionReconciler
from a13n_service.environments.websocket.relay_capability import validate_relay_backend
from a13n_service.environments.websocket.resources import ConnectionResources
from a13n_service.environments.websocket.runtime import ClientConnectionRuntime
from a13n_service.environments.websocket.service import ClientConnectionService
from a13n_service.environments.websocket.use_authorization import ClientUseAuthorization
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings
from a13n_service.storage.config import RedisServerConfig
from a13n_service.storage.redis import open_redis


async def build_environment_service(
    shared: SharedRuntime, catalog: EnvironmentProviderCatalog, settings: Settings, *, oss_identity: bool
) -> EnvironmentService:
    if oss_identity:
        await synchronize_local_providers(shared.storage.sessions, catalog, settings.environments.local_providers)
    return EnvironmentService(
        shared.storage.sessions,
        catalog,
        shared.secret_protector,
        deployment_provider_types=LOCAL_PROVIDER_TYPES if oss_identity else frozenset(),
        redis=shared.storage.redis,
    )


async def build_client_connections(
    shared: SharedRuntime,
    catalog: EnvironmentProviderCatalog,
    settings: Settings,
    environments: EnvironmentService,
    stack: AsyncExitStack,
) -> ClientConnectionRuntime | None:
    if WEBSOCKET_PROVIDER_KEY not in catalog:
        return None
    origin = settings.environments.client_public_origin
    if origin is None:
        raise ValueError("WebSocket Environments require environments.client_public_origin")
    service = ClientConnectionService(
        ConnectionResources(environments), ConnectionCoordination(shared.storage.redis), public_origin=origin
    )
    redis_config = settings.redis_config()
    if not isinstance(redis_config, RedisServerConfig):
        raise ValueError("Client WebSocket Environments require a shared Redis server")
    await validate_relay_backend(shared.storage.redis)
    # Blocking Stream reads must not consume publication and renewal capacity.
    reader = await stack.enter_async_context(
        open_redis(redis_config.model_copy(update={"max_connections": settings.environments.client_max_connections}))
    )
    return ClientConnectionRuntime(
        service,
        ClientConnectionHost(
            service,
            shared.storage.redis,
            ClientUseAuthorization(shared.storage.sessions),
            max_connections=settings.environments.client_max_connections,
            reader=reader,
        ),
        ClientConnectionReconciler(service),
    )
