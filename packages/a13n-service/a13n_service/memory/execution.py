"""Operation-owned Memory backends opened after releasing database authority checks."""

import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from a13n_harness.memory import MemoryBackend
from a13n_harness.memory_plugins import MemoryBackendCatalog
from anyio import move_on_after

from a13n_service.credentials import CredentialSnapshot
from a13n_service.secrets.crypto import SecretProtector

from .models import MemoryProviderRecord

logger = logging.getLogger("a13n_service.memory.execution")


@dataclass(frozen=True, slots=True, repr=False)
class MemoryProviderAccess:
    provider_type: str
    configuration: dict[str, object]
    credential: CredentialSnapshot

    @classmethod
    def from_record(cls, record: MemoryProviderRecord) -> "MemoryProviderAccess":
        return cls(record.type, dict(record.configuration), record.credential_snapshot())


@asynccontextmanager
async def open_memory_backend(
    access: MemoryProviderAccess, catalog: MemoryBackendCatalog, protector: SecretProtector
) -> AsyncIterator[MemoryBackend]:
    plugin = catalog[access.provider_type]
    configuration = plugin.configuration_model.model_validate(access.configuration)
    credential = plugin.credential_model.model_validate_json(access.credential.decrypt(protector))
    stack = AsyncExitStack()
    try:
        backend = await stack.enter_async_context(plugin.open(configuration, credential))
        if not isinstance(backend, MemoryBackend):
            raise TypeError("Memory Backend plugins must open a MemoryBackend")
        yield backend
    finally:
        try:
            with move_on_after(1, shield=True) as cleanup:
                await stack.aclose()
        except Exception as error:
            logger.warning(
                "Memory backend cleanup failed",
                extra={"provider_id": access.credential.resource_id, "cleanup_error_type": type(error).__name__},
            )
        else:
            if cleanup.cancel_called:
                logger.warning("Memory backend cleanup timed out", extra={"provider_id": access.credential.resource_id})
