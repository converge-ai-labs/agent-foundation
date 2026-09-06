"""Accepted-generation and per-Run composition publication services."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from a13n_ui.configuration import LoadedAgentUiConfiguration, canonical_digest
from a13n_ui.storage import LocalStore, ObjectKind, ObjectRef, ResourceIndexEntry
from a13n_ui.surfaces import RunModelOverrides

from .models import ResolvedAgentNode, ResolvedRunComposition
from .resolver import AgentCompositionResolver, ThreadCompositionSelection


@dataclass(frozen=True, slots=True)
class AcceptedComposition:
    source_digest: str
    generation: ObjectRef


@dataclass(frozen=True, slots=True)
class PublishedRunComposition:
    value: ResolvedRunComposition
    reference: ObjectRef


class CompositionAcceptanceService:
    """Publish complete validated source generations and select their SQLite head."""

    def __init__(self, store: LocalStore, resolver: AgentCompositionResolver) -> None:
        self._store = store
        self._resolver = resolver

    def validate(self, source: LoadedAgentUiConfiguration) -> None:
        """Validate one complete candidate without publishing or selecting it."""

        self._resolver.validate_generation(source)

    async def accept(
        self,
        source: LoadedAgentUiConfiguration,
        *,
        expected_current_digest: str | None,
    ) -> AcceptedComposition:
        self.validate(source)
        envelope = await self._store.objects.publish_model(
            object_kind=ObjectKind.configuration_generation,
            value=source,
        )
        source_rows = tuple(
            (item.relative_path, item.source_digest, item.resource_kind, item.resource_id) for item in source.sources
        )
        indexes = tuple(
            ResourceIndexEntry(
                generation_digest=source.source_digest,
                resource_kind=item.resource_kind,
                resource_id=item.resource_id,
                name=_resource_name(source, item.resource_kind, item.resource_id),
                relative_path=item.relative_path,
                source_digest=item.source_digest,
                normalized_digest=canonical_digest(_resource_value(source, item.resource_kind, item.resource_id)),
            )
            for item in source.sources
            if item.resource_id is not None
        )
        await self._store.configurations.accept(
            generation_digest=source.source_digest,
            generation=envelope.ref,
            sources=source_rows,
            resources=indexes,
            expected_current_digest=expected_current_digest,
        )
        return AcceptedComposition(source_digest=source.source_digest, generation=envelope.ref)

    async def current(self) -> LoadedAgentUiConfiguration | None:
        reference = await self._store.configurations.current_reference()
        if reference is None:
            return None
        return await self._store.objects.read_model(reference, LoadedAgentUiConfiguration)

    async def load(self, generation_digest: str) -> LoadedAgentUiConfiguration:
        reference = await self._store.configurations.reference(generation_digest)
        if reference is None:
            raise ValueError("accepted configuration generation does not exist")
        return await self._store.objects.read_model(reference, LoadedAgentUiConfiguration)


class RunCompositionService:
    """Resolve and publish the exact immutable composition for one admitted Run."""

    def __init__(self, store: LocalStore, resolver: AgentCompositionResolver) -> None:
        self._store = store
        self._resolver = resolver

    async def publish(
        self,
        source: LoadedAgentUiConfiguration,
        selection: ThreadCompositionSelection,
        *,
        parent_node: ResolvedAgentNode | None = None,
        model_overrides: RunModelOverrides | None = None,
    ) -> PublishedRunComposition:
        value = self._resolver.resolve_run(source, selection, parent_node=parent_node, model_overrides=model_overrides)
        envelope = await self._store.objects.publish_model(
            object_kind=ObjectKind.run_composition,
            value=value,
        )
        return PublishedRunComposition(value=value, reference=envelope.ref)


def _resource_value(source: LoadedAgentUiConfiguration, kind: str, resource_id: str) -> object:
    resources = {
        "model": source.models,
        "harness_plugin": source.harness_plugins,
        "environment_profile": source.environment_profiles,
        "environment_run_extension": source.environment_run_extensions,
        "mcp_server": source.mcp_servers,
        "agent": source.agents,
        "subagent": source.subagents,
        "project": source.projects,
    }.get(kind)
    if resources is None:
        raise ValueError(f"unsupported resource kind: {kind}")
    return resources[resource_id]


def _resource_name(source: LoadedAgentUiConfiguration, kind: str, resource_id: str) -> str:
    value = _resource_value(source, kind, resource_id)
    if not isinstance(value, BaseModel):
        raise TypeError("resource must be a Pydantic model")
    name = value.model_dump(mode="python").get("name")
    if not isinstance(name, str):
        raise TypeError("resource name must be a string")
    return name


__all__ = [
    "AcceptedComposition",
    "CompositionAcceptanceService",
    "PublishedRunComposition",
    "RunCompositionService",
]
