"""Compose Environment authoring and OSS deployment-owned local Providers."""

from a13n_environment import EnvironmentProviderCatalog

from a13n_service.environments.domain import LOCAL_PROVIDER_TYPES
from a13n_service.environments.local import synchronize_local_providers
from a13n_service.environments.service import EnvironmentService
from a13n_service.process.runtime import SharedRuntime
from a13n_service.settings import Settings


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
    )
