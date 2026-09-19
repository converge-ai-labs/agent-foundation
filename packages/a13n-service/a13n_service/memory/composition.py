"""Compose managed Memory resources without opening backend clients."""

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory import MemoryProviderDefinition
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.configuration.sections import MemorySettings
from a13n_service.secrets.crypto import SecretProtector

from .scopes import MemoryAuthorizer
from .service import MemoryService


def build_memory_service(
    settings: MemorySettings,
    sessions: async_sessionmaker[AsyncSession],
    protector: SecretProtector,
    catalog: ProviderCatalog[MemoryProviderDefinition],
) -> MemoryService:
    return MemoryService(
        catalog,
        protector,
        MemoryAuthorizer(sessions, catalog),
        timeout=settings.timeout_seconds,
    )
