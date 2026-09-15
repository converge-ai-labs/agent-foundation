"""Borrowed native backends for tests of managed Service composition."""

from contextlib import asynccontextmanager

from a13n_harness.memory import MemoryBackend, MemoryDocumentBackend
from a13n_harness.memory_plugins import Mem0Credential, MemoryBackendCatalog, MemoryBackendPlugin
from a13n_service.memory.domain import CreateMemoryProviderRequest
from a13n_service.memory.providers import MemoryProviderService
from a13n_service.memory.scopes import MemoryAuthorizer
from a13n_service.memory.service import MemoryService
from pydantic import BaseModel, ConfigDict
from tests.models.conftest import WORKSPACE_ID, actor, protector


class EmptyConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BorrowedMemoryPlugin(MemoryBackendPlugin[EmptyConfiguration, Mem0Credential]):
    key = "test.memory"
    display_name = "Test memory"
    configuration_model = EmptyConfiguration
    credential_model = Mem0Credential

    def __init__(self, backend: MemoryBackend):
        self.supports_documents = isinstance(backend, MemoryDocumentBackend)
        self.backend = backend
        self.credentials = []
        self.opened = 0
        self.closed = 0

    @asynccontextmanager
    async def open(self, configuration, credential):
        self.credentials.append(credential.api_key.get_secret_value())
        self.opened += 1
        try:
            yield self.backend
        finally:
            self.closed += 1


async def memory_service(sessions, backend, *, timeout=30, principal=None, workspace_id=WORKSPACE_ID):
    plugin = BorrowedMemoryPlugin(backend)
    catalog = MemoryBackendCatalog((plugin,))
    protection = protector()
    providers = MemoryProviderService(sessions, protection, catalog)
    provider = await providers.create(
        actor=principal or actor(),
        workspace_id=workspace_id,
        request=CreateMemoryProviderRequest(type=plugin.key, name="Memory", credential={"api_key": "first-key"}),
    )
    service = MemoryService(catalog, protection, MemoryAuthorizer(sessions, catalog), timeout=timeout)
    return service, provider, plugin
