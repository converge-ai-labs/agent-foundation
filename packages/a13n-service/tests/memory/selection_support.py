"""Ordinary-only execution composition for Service tests."""

from a13n_harness.memory_plugins import MemoryBackendCatalog
from a13n_service.memory.behaviors import MemoryBehaviors
from a13n_service.memory.ordinary import OrdinaryMemory
from a13n_service.memory.scopes import MemoryAuthorizer
from a13n_service.memory.service import MemoryService
from a13n_service.secrets import SecretProtector


def ordinary_memory(sessions):
    catalog = MemoryBackendCatalog(())
    service = MemoryService(
        catalog, SecretProtector(key=b"k" * 32, encryption_key_id="test"), MemoryAuthorizer(sessions, catalog)
    )
    return MemoryBehaviors(sessions, default=OrdinaryMemory(service))
