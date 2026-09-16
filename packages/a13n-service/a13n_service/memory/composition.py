"""Compose managed Memory resources without opening backend clients."""

from typing import TYPE_CHECKING

from a13n_harness.memory_plugins import MemoryBackendCatalog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.configuration.sections import MemorySettings
from a13n_service.secrets.crypto import SecretProtector

from .scopes import MemoryAuthorizer
from .service import MemoryService

if TYPE_CHECKING:
    from .bots.verification import BotMemoryVerifier


def build_memory_service(
    settings: MemorySettings,
    sessions: async_sessionmaker[AsyncSession],
    protector: SecretProtector,
    catalog: MemoryBackendCatalog,
    *,
    bot_verifier: "BotMemoryVerifier | None" = None,
) -> MemoryService:
    return MemoryService(
        catalog,
        protector,
        MemoryAuthorizer(sessions, catalog),
        timeout=settings.timeout_seconds,
        bot_verifier=bot_verifier,
    )
