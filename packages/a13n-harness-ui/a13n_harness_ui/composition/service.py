"""Accepted-generation and per-Run composition publication services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from a13n_harness import RunConfiguration
from a13n_logging import get_logger
from anyio import Lock, to_thread
from pydantic import BaseModel

from a13n_harness_ui.configuration import LoadedHarnessUiConfiguration, canonical_digest
from a13n_harness_ui.connectors.models import Capture as ConnectorCapture
from a13n_harness_ui.mcp_oauth.service import McpOAuth
from a13n_harness_ui.storage import LocalStore, ObjectKind, ObjectRef, ResourceIndexEntry, Thread
from a13n_harness_ui.surfaces import RunModelOverrides

from .models import ResolvedAgentNode, ResolvedModelRecipe, ResolvedRunComposition
from .resolver import AgentCompositionResolver, ThreadCompositionSelection

_LOGGER = get_logger(__name__)


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
        self.capability_warnings: tuple[str, ...] = ()
        self._cached: tuple[ObjectRef, LoadedHarnessUiConfiguration] | None = None
        self._cache_lock = Lock()

    def validate(self, source: LoadedHarnessUiConfiguration) -> None:
        """Validate one complete candidate without publishing or selecting it."""

        self._resolver.validate_generation(source, warnings=[])

    async def accept(
        self,
        source: LoadedHarnessUiConfiguration,
        *,
        expected_current_digest: str | None,
    ) -> AcceptedComposition:
        warnings: list[str] = []
        self._resolver.validate_generation(source, warnings=warnings)
        envelope = await self._store.objects.publish_model(
            object_kind=ObjectKind.configuration_generation,
            value=source,
        )
        reference = envelope.ref
        source_rows = tuple(
            (item.relative_path, item.source_digest, item.resource_kind, item.resource_id) for item in source.sources
        )
        indexes = tuple(
            ResourceIndexEntry(
                generation_digest=source.source_digest,
                resource_kind=item.resource_kind,
                resource_id=resource_id,
                name=_resource_name(source, item.resource_kind, resource_id),
                relative_path=item.relative_path,
                source_digest=item.source_digest,
                normalized_digest=canonical_digest(_resource_value(source, item.resource_kind, resource_id)),
            )
            for item in source.sources
            for resource_id in item.indexed_resource_ids
        )
        await self._store.configurations.accept(
            generation_digest=source.source_digest,
            generation=reference,
            sources=source_rows,
            resources=indexes,
            expected_current_digest=expected_current_digest,
        )
        self.capability_warnings = tuple(warnings)
        for warning in warnings:
            _LOGGER.warning("capability_skipped", extra={"warning": warning})
        return AcceptedComposition(source_digest=source.source_digest, generation=reference)

    async def current(self) -> LoadedHarnessUiConfiguration | None:
        reference = await self._store.configurations.current_reference()
        if reference is None:
            return None
        return await self._load_reference(reference)

    async def load(self, generation_digest: str) -> LoadedHarnessUiConfiguration:
        reference = await self._store.configurations.reference(generation_digest)
        if reference is None:
            raise ValueError("accepted configuration generation does not exist")
        return await self._load_reference(reference)

    async def _load_reference(self, reference: ObjectRef) -> LoadedHarnessUiConfiguration:
        # Recheck the selected SQLite head on every call; cache only verified immutable data.
        async with self._cache_lock:
            if self._cached is None or self._cached[0] != reference:
                value = await self._store.objects.read_model(reference, LoadedHarnessUiConfiguration)
                self._cached = (reference, value)
            cached = self._cached[1]
        # Frozen models still contain mutable mappings. Never lend the cached instance.
        return await to_thread.run_sync(lambda: cached.model_copy(deep=True))


class RunCompositionService:
    """Resolve and publish the exact immutable composition for one admitted Run."""

    def __init__(self, store: LocalStore, resolver: AgentCompositionResolver, *, oauth: McpOAuth | None = None) -> None:
        self._store = store
        self._resolver = resolver
        self._oauth = oauth
        self.connector_capture: Callable[[], Awaitable[tuple[ConnectorCapture, ...]]] | None = None

    def resolve_model(self, source: LoadedHarnessUiConfiguration, model_id: str) -> ResolvedModelRecipe:
        return self._resolver.model_recipe(source.models[model_id], source)

    async def publish_memory(self, source: LoadedHarnessUiConfiguration, thread: Thread) -> PublishedRunComposition:
        value = self._resolver.resolve_memory(source, thread)
        if self._oauth is not None:
            value = value.model_copy(update={"root": await self._oauth.bind_node(value.root)})
        envelope = await self._store.objects.publish_model(object_kind=ObjectKind.run_composition, value=value)
        return PublishedRunComposition(value=value, reference=envelope.ref)

    async def publish(
        self,
        source: LoadedHarnessUiConfiguration,
        selection: ThreadCompositionSelection,
        *,
        parent_node: ResolvedAgentNode | None = None,
        dynamic_node: ResolvedAgentNode | None = None,
        model_overrides: RunModelOverrides | None = None,
        run_configuration: RunConfiguration | None = None,
    ) -> PublishedRunComposition:
        value = self._resolver.resolve_run(
            source,
            selection,
            parent_node=parent_node,
            dynamic_node=dynamic_node,
            model_overrides=model_overrides,
            run_configuration=run_configuration,
        )
        if (
            self.connector_capture is not None
            and parent_node is None
            and value.root.source_kind not in {"live", "memory"}
        ):
            value = value.model_copy(update={"connectors": await self.connector_capture()})
        if self._oauth is not None and parent_node is None:
            value = value.model_copy(update={"root": await self._oauth.bind_node(value.root)})
        envelope = await self._store.objects.publish_model(
            object_kind=ObjectKind.run_composition,
            value=value,
        )
        return PublishedRunComposition(value=value, reference=envelope.ref)


def _resource_value(source: LoadedHarnessUiConfiguration, kind: str, resource_id: str) -> object:
    resources = {
        "model": source.models,
        "subscription_group": source.subscription_groups,
        "harness_plugin": source.harness_plugins,
        "environment_profile": source.environment_profiles,
        "environment_run_extension": source.environment_run_extensions,
        "mcp_server": source.mcp_servers,
        "agent": source.agents,
        "subagent": source.subagents,
        "project": source.projects,
        "device": source.devices,
    }.get(kind)
    if resources is None:
        raise ValueError(f"unsupported resource kind: {kind}")
    return resources[resource_id]


def _resource_name(source: LoadedHarnessUiConfiguration, kind: str, resource_id: str) -> str:
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
