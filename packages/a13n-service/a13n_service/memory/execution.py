"""Operation-owned Memory backends opened after releasing database authority checks."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a13n_harness.providers.catalog import ProviderCatalog
from a13n_harness.providers.memory import MemoryProviderDefinition
from a13n_harness.providers.memory.contracts import MemoryBackend

from a13n_service.credentials import CredentialSnapshot
from a13n_service.secrets.crypto import SecretProtector

from .models import MemoryProviderRecord


@dataclass(frozen=True, slots=True, repr=False)
class MemoryProviderAccess:
    provider_type: str
    configuration: dict[str, object]
    provider_id: str
    credential: CredentialSnapshot | None

    @classmethod
    def from_record(cls, record: MemoryProviderRecord) -> "MemoryProviderAccess":
        return cls(
            record.type,
            dict(record.configuration),
            record.id,
            record.credential_snapshot() if record.ciphertext is not None else None,
        )


@asynccontextmanager
async def open_memory_backend(
    access: MemoryProviderAccess, catalog: ProviderCatalog[MemoryProviderDefinition], protector: SecretProtector
) -> AsyncIterator[MemoryBackend]:
    definition = catalog[access.provider_type]
    credential = json.loads(access.credential.decrypt(protector)) if access.credential is not None else None
    async with definition.open(access.configuration, credential) as backend:
        yield backend
