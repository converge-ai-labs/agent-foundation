"""Ordinary Agent memory behavior, independent of application-specific selections."""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run
from a13n_service.interactions.ports.memory import ActiveMemory, DisabledMemory

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
        pass

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
