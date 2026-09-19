"""Conversation document behavior contributed by the Bot application."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run
from a13n_service.interactions.errors import RunAcceptanceError
from a13n_service.interactions.ports.memory import ActiveMemory, DisabledMemory
from a13n_service.memory.models import MemoryProviderRecord, RunMemoryStorageRecord
from a13n_service.memory.resources import binds_host_files
from a13n_service.memory.service import MemoryService
from a13n_service.storage import short_session

from .binding import BotMemoryBinding
from .bindings import BEHAVIOR_KEY, SCHEMA_VERSION, RunMemoryBindingRecord, require_binding
from .runtime import bot_memory_capability
from .verification import BotMemoryVerifier


@dataclass(frozen=True)
class PreparedConversationMemory:
    service: MemoryService
    run: Run
    binding: BotMemoryBinding
    verifier: BotMemoryVerifier | None
    current_context: Callable[[], AttemptContext]
    filesystem: bool = False

    def for_node(self, node: AgentDefinitionReconstructionContext) -> ActiveMemory | DisabledMemory:
        capability = bot_memory_capability(
            self.service,
            run=self.run,
            binding=self.binding,
            verifier=self.verifier,
            agent_id=node.agent_id,
            current_context=self.current_context,
            filesystem=self.filesystem,
        )
        return DisabledMemory() if capability is None else ActiveMemory(capability)


class ConversationMemory:
    key = BEHAVIOR_KEY
    schema_version = SCHEMA_VERSION

    def __init__(self, service: MemoryService, verifier: BotMemoryVerifier | None) -> None:
        self.service, self.verifier = service, verifier

    async def validate(self, session: AsyncSession, run_id: str) -> None:
        await require_binding(session, run_id)

    async def inherit(self, session: AsyncSession, source_run_id: str, run_id: str) -> None:
        binding = await require_binding(session, source_run_id)
        session.add(RunMemoryBindingRecord(run_id=run_id, **binding.model_dump()))
        for stored in await session.scalars(
            select(RunMemoryStorageRecord).where(RunMemoryStorageRecord.run_id == source_run_id)
        ):
            session.add(
                RunMemoryStorageRecord(
                    run_id=run_id,
                    selection_digest=stored.selection_digest,
                    storage_id=stored.storage_id,
                    organization_policy=stored.organization_policy,
                )
            )

    async def prepare(
        self,
        *,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        current_context: Callable[[], AttemptContext],
    ) -> PreparedConversationMemory:
        async with short_session(self.service.authorizer.sessions) as session:
            binding = await require_binding(session, run.id)
            provider = await session.get(MemoryProviderRecord, binding.provider_id) if binding.provider_id else None
            filesystem = provider is not None and binds_host_files(provider.type, self.service.catalog)
        if (binding.use_memory or binding.save_on_request) and self.verifier is None:
            raise RunAcceptanceError("memory_binding_unavailable", "Conversation verification is unavailable")
        return PreparedConversationMemory(self.service, run, binding, self.verifier, current_context, filesystem)
