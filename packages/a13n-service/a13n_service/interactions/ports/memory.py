"""Memory behavior supplied by composition, independent of storage and applications."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from a13n_harness.capabilities.memory import MemoryCapability
from sqlalchemy.ext.asyncio import AsyncSession

from a13n_service.agents.domain import EffectiveAgentConfig
from a13n_service.agents.reconstruction import AgentDefinitionReconstructionContext
from a13n_service.interactions.attempts import AttemptContext
from a13n_service.interactions.domain import Run


class ExecutionBindings(Protocol):
    async def finalize(self, session: AsyncSession, run: Run, *, source_run_id: str | None) -> None:
        """Retain or finalize bindings inside the caller's acceptance transaction."""
        ...

    async def validate(self, session: AsyncSession, run_id: str) -> None:
        """Reject incomplete accepted state, including idempotent replay."""
        ...


MemoryOperation = Literal["index", "list", "search", "read", "create", "delete"]


@dataclass(frozen=True)
class DisabledMemory:
    """Terminal behavior selection; never an invitation to try another provider."""


@dataclass(frozen=True)
class ActiveMemory:
    capability: MemoryCapability

    @property
    def retrieval(self) -> Literal["automatic", "index_first", "on_request"]:
        if self.capability.document_store is not None:
            return "index_first"
        return "automatic" if self.capability.auto_recall else "on_request"

    @property
    def operations(self) -> frozenset[MemoryOperation]:
        capability = self.capability
        if capability.document_store is not None:
            operations: set[MemoryOperation] = set()
            if capability.document_read:
                operations.update(("index", "search", "read"))
            if capability.document_write:
                operations.update(("create", "delete"))
            return frozenset(operations)
        return frozenset(("search", "list", "create")) if capability.toolset else frozenset()


class PreparedMemory(Protocol):
    def for_node(self, node: AgentDefinitionReconstructionContext) -> DisabledMemory | ActiveMemory: ...


class ExecutionMemoryRuntime(Protocol):
    async def prepare(
        self,
        *,
        run: Run,
        workspace_id: str,
        config: EffectiveAgentConfig,
        current_context: Callable[[], AttemptContext],
    ) -> PreparedMemory: ...
