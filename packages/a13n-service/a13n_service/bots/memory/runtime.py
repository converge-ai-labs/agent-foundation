"""Bind model document tools to the accepted conversation, never model-selected metadata."""

from collections.abc import Callable
from typing import Literal

from a13n_harness.capabilities.memory import MemoryCapability
from a13n_harness.memory_documents import (
    MemoryDocumentContent,
    MemoryDocumentIndex,
    MemoryDocumentReference,
    MemoryDocumentStore,
)

from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run
from a13n_service.memory.service import MemoryService, failure

from .access import RuntimeAuthority
from .binding import BotMemoryBinding
from .domain import CreateDocument, SearchDocuments
from .mutations import create, delete
from .service import BotMemoryService
from .verification import BotMemoryVerifier


class ConversationDocumentStore(MemoryDocumentStore):
    def __init__(
        self, service: BotMemoryService, authority: RuntimeAuthority, run_id: str, verifier: BotMemoryVerifier | None
    ) -> None:
        self.service, self.authority, self.run_id = service, authority, run_id
        self.verifier = verifier

    async def _verified(self, *, write: bool = False, document_id: str | None = None) -> RuntimeAuthority:
        verifier = self.verifier
        if verifier is None:
            raise failure("memory_scope_unverified", "Live conversation verification is unavailable.")
        return await verifier.verify(self.authority, write=write, document_id=document_id)

    async def index(self, *, cursor: str | None = None) -> MemoryDocumentIndex:
        authority = await self._verified()
        result = await self.service.index(authority, authority.account_id, authority.scope_id, cursor=cursor)
        return MemoryDocumentIndex(result.text, result.next_cursor)

    async def read(self, document_id: str) -> MemoryDocumentContent:
        authority = await self._verified(document_id=document_id)
        result = await self.service.get(authority, authority.account_id, authority.scope_id, document_id)
        return MemoryDocumentContent(result.id, result.title, result.text)

    async def search(self, query: str, *, limit: int) -> tuple[MemoryDocumentReference, ...]:
        authority = await self._verified()
        result = await self.service.search(
            authority,
            authority.account_id,
            authority.scope_id,
            SearchDocuments(query=query, limit=limit),
        )
        return tuple(MemoryDocumentReference(item.id, item.title, item.description) for item in result.items)

    async def create(
        self,
        text: str,
        *,
        title: str,
        description: str,
        kind: Literal["daily", "long_term"],
        correction_of: str | None,
        request_key: str,
    ) -> MemoryDocumentReference:
        authority = await self._verified(write=True)
        result = await create(
            self.service,
            authority,
            authority.account_id,
            authority.scope_id,
            CreateDocument(text=text, title=title, description=description, kind=kind, correction_of=correction_of),
            f"run:{self.run_id}:tool:{request_key}",
        )
        return MemoryDocumentReference(result.id, result.title, result.description)

    async def delete(self, document_id: str) -> None:
        authority = await self._verified(write=True)
        await delete(self.service, authority, authority.account_id, authority.scope_id, document_id)


def bot_memory_capability(
    service: MemoryService,
    *,
    run: Run,
    binding: BotMemoryBinding,
    verifier: BotMemoryVerifier | None,
    agent_id: str,
    current_context: Callable[[], AttemptContext],
) -> MemoryCapability | None:
    if not (binding.use_memory or binding.save_on_request):
        return None
    assert binding.scope_id is not None and binding.provider_id is not None
    authority = RuntimeAuthority(binding.account_id, binding.scope_id, binding.provider_id, agent_id, current_context)
    return MemoryCapability(
        document_store=ConversationDocumentStore(BotMemoryService(service), authority, run.id, verifier),
        document_read=binding.use_memory,
        document_write=binding.save_on_request,
    )
