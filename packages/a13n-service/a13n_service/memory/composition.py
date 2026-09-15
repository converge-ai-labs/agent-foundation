"""Process-owned native Mem0 clients shared by control and worker."""

from contextlib import AsyncExitStack

from a13n_harness.capabilities.mem0_backends import open_mem0_oss, open_mem0_platform
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from a13n_service.configuration.sections import MemorySettings

from .scopes import MemoryAuthorizer
from .service import MemoryService, NativeBackend


async def build_memory_service(
    settings: MemorySettings, sessions: async_sessionmaker[AsyncSession], stack: AsyncExitStack
) -> MemoryService:
    backend: NativeBackend | None = None
    if settings.provider != "none":
        assert settings.api_key is not None
        key = settings.api_key.get_secret_value()
        if settings.provider == "oss":
            assert settings.base_url is not None
            backend = await stack.enter_async_context(
                open_mem0_oss(base_url=settings.base_url, api_key=key, timeout=settings.timeout_seconds)
            )
        else:
            backend = await stack.enter_async_context(open_mem0_platform(api_key=key, base_url=settings.base_url))
    return MemoryService(backend, MemoryAuthorizer(sessions), timeout=settings.timeout_seconds)
