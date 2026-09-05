"""Compose Environment authoring from shared infrastructure."""

from a13n_environment_provider import EnvironmentProviderCatalog

from a13n_service.environments.service import EnvironmentService
from a13n_service.process.runtime import SharedRuntime


def build_environment_service(shared: SharedRuntime, catalog: EnvironmentProviderCatalog) -> EnvironmentService:
    return EnvironmentService(shared.storage.sessions, catalog, shared.secret_protector)
