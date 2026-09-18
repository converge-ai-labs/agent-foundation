"""Ordinary Agent memory behavior, independent of application-specific selections."""

from collections.abc import Callable
from dataclasses import dataclass
from functools import partial

from a13n_harness.capabilities.memory import MemoryCapability, MemoryEntry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run
from a13n_service.interactions.ports.memory import ActiveMemory, DisabledMemory

from .domain import ManagedMemoryBackend, MemoryEntries, MemorySelection
from .file_runtime import filesystem_store
from .models import RunMemoryStorageRecord
from .runtime import memory_capability, validate_memory_providers
from .service import MemoryService


@dataclass(frozen=True)
class OrdinaryPreparedMemory:
    service: MemoryService
    run: Run
    workspace_id: str
    current_context: Callable[[], AttemptContext]

    def for_node(self, node: AgentDefinitionReconstructionContext) -> ActiveMemory | DisabledMemory:
        if node.config.memory is None:
            return DisabledMemory()
        if isinstance(node.config.memory, MemoryEntries):
            entries = []
            for entry in node.config.memory.entries:
                if entry.mode == "records":
                    if not isinstance(entry.backend, ManagedMemoryBackend):
                        raise ValueError("Records require a managed backend")
                    selection = MemorySelection(
                        provider_id=entry.backend.provider_id,
                        **entry.model_dump(
                            include={
                                "scope",
                                "toolset",
                                "recall_required",
                                "auto_recall",
                                "recall_limit",
                                "recall_threshold",
                                "recall_timeout",
                            }
                        ),
                    )
                    child = memory_capability(
                        self.service,
                        run=self.run,
                        workspace_id=self.workspace_id,
                        agent_id=node.agent_id,
                        selection=selection,
                        current_context=self.current_context,
                    )
                else:
                    child = MemoryCapability(
                        document_factory=partial(
                            filesystem_store,
                            service=self.service,
                            run=self.run,
                            workspace_id=self.workspace_id,
                            agent_id=node.agent_id,
                            entry=entry,
                            current_context=self.current_context,
                        ),
                        toolset=entry.toolset,
                        recall_required=entry.recall_required,
                    )
                entries.append(
                    MemoryEntry(name=entry.name, mode=entry.mode, description=entry.description, capability=child)
                )
            return ActiveMemory(MemoryCapability(entries=entries))
        capability = memory_capability(
            self.service,
            run=self.run,
            workspace_id=self.workspace_id,
            agent_id=node.agent_id,
            selection=node.config.memory,
            current_context=self.current_context,
        )
        return ActiveMemory(capability)


class OrdinaryMemory:
    key = "agent"
    schema_version = 1

    def __init__(self, service: MemoryService) -> None:
        self.service = service

    async def validate(self, session: AsyncSession, run_id: str) -> None:
        pass  # Ordinary selection uses the existing frozen Agent definition graph.

    async def inherit(self, session: AsyncSession, source_run_id: str, run_id: str) -> None:
        rows = (
            await session.scalars(select(RunMemoryStorageRecord).where(RunMemoryStorageRecord.run_id == source_run_id))
        ).all()
        for row in rows:
            session.add(
                RunMemoryStorageRecord(
                    run_id=run_id,
                    selection_digest=row.selection_digest,
                    storage_id=row.storage_id,
                    organization_policy=row.organization_policy,
                )
            )

    async def prepare(
        self,
        *,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        current_context: Callable[[], AttemptContext],
    ) -> OrdinaryPreparedMemory:
        await validate_memory_providers(
            self.service, organization_id=run.organization_id, workspace_id=workspace_id, config=config
        )
        return OrdinaryPreparedMemory(self.service, run, workspace_id, current_context)
