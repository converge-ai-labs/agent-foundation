"""Application service for exact composition resolution and reconstruction."""

from __future__ import annotations

from typing import Any

from a13n_harness import ExecutableAgent

from a13n_ui.configuration import CatalogRepository, ConfigurationGeneration
from a13n_ui.errors import CompositionError
from a13n_ui.storage import LocalStore

from .models import (
    AgentEnvironmentCompatibility,
    ResolvedAgentSnapshot,
    ResolvedEnvironmentSnapshot,
    SnapshotReference,
)
from .reconstruction import AgentReconstructor, ExecutableCache
from .repository import SnapshotRepository
from .resolver import SnapshotResolver, validate_compatibility


class CompositionService:
    """Coordinate generation capture, durable snapshots, and native reconstruction."""

    def __init__(self, store: LocalStore, catalog: CatalogRepository) -> None:
        self._catalog = catalog
        self._repository = SnapshotRepository(store)
        self._resolver = SnapshotResolver(catalog, data_root=store.layout.root)
        self._cache = ExecutableCache()
        self._reconstructor = AgentReconstructor(catalog, self._cache)

    async def resolve_agent(
        self,
        agent_id: str,
        *,
        generation_id: str | None = None,
    ) -> SnapshotReference:
        generation = await self._generation(generation_id)
        snapshot = await self._resolver.resolve_agent(generation, agent_id)
        return await self._repository.publish_agent(snapshot)

    async def resolve_environment(
        self,
        environment_id: str,
        *,
        generation_id: str | None = None,
    ) -> SnapshotReference:
        generation = await self._generation(generation_id)
        settings = await self._catalog.generation_settings(generation.generation_id)
        snapshot = await self._resolver.resolve_environment(
            generation,
            environment_id,
            settings,
        )
        return await self._repository.publish_environment(snapshot)

    async def agent(self, reference: SnapshotReference) -> ResolvedAgentSnapshot:
        return await self._repository.agent(reference)

    async def environment(
        self,
        reference: SnapshotReference,
    ) -> ResolvedEnvironmentSnapshot:
        return await self._repository.environment(reference)

    async def compatibility(
        self,
        agent_reference: SnapshotReference,
        environment_reference: SnapshotReference,
    ) -> AgentEnvironmentCompatibility:
        agent = await self.agent(agent_reference)
        environment = await self.environment(environment_reference)
        return validate_compatibility(agent, environment)

    async def executable(
        self,
        reference: SnapshotReference,
        environment_reference: SnapshotReference | None = None,
    ) -> ExecutableAgent[Any]:
        """Return the process-owned executable for one validated snapshot pairing."""

        agent = await self.agent(reference)
        environment = None
        if environment_reference is not None:
            environment = await self.environment(environment_reference)
            validate_compatibility(agent, environment)
        return await self._reconstructor.executable(agent, environment)

    async def validate_executable(
        self,
        reference: SnapshotReference,
        environment_reference: SnapshotReference | None = None,
    ) -> None:
        """Build one retained Agent through the ordinary cached reconstruction path."""

        await self.executable(reference, environment_reference)

    async def close(self) -> None:
        await self._cache.close()

    async def _generation(self, generation_id: str | None) -> ConfigurationGeneration:
        if generation_id is not None:
            return await self._catalog.generation(generation_id)
        generation = await self._catalog.current_generation()
        if generation is None:
            raise CompositionError(
                "Composition resolution requires an accepted configuration generation.",
                code="configuration_generation_missing",
            )
        return generation


__all__ = ["CompositionService"]
