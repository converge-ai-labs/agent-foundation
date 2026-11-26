"""Accepted-generation and per-Run composition publication services."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from a13n_logging import get_logger
from anyio import Lock, to_thread
from pydantic import BaseModel

from a13n_harness_ui.configuration import LoadedHarnessUiConfiguration, canonical_digest
from a13n_harness_ui.connectors.models import Capture as ConnectorCapture
from a13n_harness_ui.errors import CompositionError, ConfigurationError
from a13n_harness_ui.extensions.environment_adapters import LocalEnvdProjectAdapter
from a13n_harness_ui.mcp_oauth.service import McpOAuth
from a13n_harness_ui.model_thinking import ThinkingSelection, apply_thinking
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

    async def creation_model(
        self,
        composition: ResolvedRunComposition | None,
        model: str | None,
        thinking: ThinkingSelection | None,
        *,
        current_generation: bool = False,
    ) -> ResolvedModelRecipe:
        source = (
            await self.current()
            if composition is None or current_generation
            else await self.load(composition.generation_digest)
        )
        if source is None:
            raise CompositionError("No configuration has been accepted.", code="configuration_missing")
        policy = source.document.agent_creation if composition is None else composition.agent_creation
        selected = policy.select_model(model)
        if selected not in source.models:
            raise ConfigurationError(
                f"The selected creation model {selected!r} is no longer configured.", code="creation_model_missing"
            )
        recipe = self._resolver.model_recipe(source.models[selected], source)
        if thinking is None:
            return recipe
        settings = apply_thinking(recipe.route, recipe.settings, thinking)
        return recipe.model_copy(update={"settings": settings, "thinking_override": thinking})

    async def validate_model_thinking(self, model_id: str, thinking: ThinkingSelection) -> ResolvedModelRecipe:
        source = await self.current()
        if source is None:
            raise CompositionError("No configuration has been accepted.", code="configuration_missing")
        if model_id not in source.models:
            raise ConfigurationError(
                f"The selected model {model_id!r} is no longer configured.", code="creation_model_missing"
            )
        recipe = self._resolver.model_recipe(source.models[model_id], source)
        settings = apply_thinking(recipe.route, recipe.settings, thinking)
        return recipe.model_copy(update={"settings": settings, "thinking_override": thinking})

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
                value = await self._store.objects.read_model(
                    reference, LoadedHarnessUiConfiguration, context={"stored_generation": True}
                )
                self._cached = (reference, value)
            cached = self._cached[1]
        # Frozen models still contain mutable mappings. Never lend the cached instance.
        return await to_thread.run_sync(lambda: cached.model_copy(deep=True))


class RunCompositionService:
    """Resolve and publish the exact immutable composition for one admitted Run."""

    def __init__(
        self,
        store: LocalStore,
        resolver: AgentCompositionResolver,
        configurations: CompositionAcceptanceService,
        *,
        oauth: McpOAuth | None = None,
    ) -> None:
        self._store = store
        self._resolver = resolver
        self._configurations = configurations
        self._oauth = oauth
        self.connector_capture: Callable[[], Awaitable[tuple[ConnectorCapture, ...]]] | None = None

    async def publish_memory(self, source: LoadedHarnessUiConfiguration, thread: Thread) -> PublishedRunComposition:
        value = self._resolver.resolve_memory(source, thread)
        return await self.save(await self._bind_oauth(value))

    async def publish(
        self,
        source: LoadedHarnessUiConfiguration,
        selection: ThreadCompositionSelection,
        *,
        model_overrides: RunModelOverrides | None = None,
    ) -> PublishedRunComposition:
        return await self.save(await self.resolve(source, selection, model_overrides=model_overrides))

    async def resolve(
        self,
        source: LoadedHarnessUiConfiguration,
        selection: ThreadCompositionSelection,
        *,
        model_overrides: RunModelOverrides | None = None,
    ) -> ResolvedRunComposition:
        value = self._resolver.resolve_run(source, selection, model_overrides=model_overrides)
        if self.connector_capture is not None and value.root.source_kind not in {"live", "memory"}:
            value = value.model_copy(update={"connectors": await self.connector_capture()})
        return await self._bind_oauth(value)

    async def resolve_child_resume(
        self,
        source: LoadedHarnessUiConfiguration,
        selection: ThreadCompositionSelection,
        *,
        parent: ResolvedRunComposition,
        dynamic_node: ResolvedAgentNode | None,
    ) -> ResolvedRunComposition:
        value = self._resolver.resolve_run(
            source,
            selection,
            parent_node=parent.require_native_root(),
            dynamic_node=dynamic_node,
            run_configuration=parent.run_configuration,
        )
        return await self._bind_oauth(value)

    async def derive_child(
        self,
        *,
        parent: ResolvedRunComposition,
        root: ResolvedAgentNode,
        thread_id: str,
        package_prompt_revision: str | None = None,
    ) -> ResolvedRunComposition:
        """Project a captured child policy without inheriting parent-only authority or bindings."""
        profile = parent.base_environment_profile or parent.environment_profile
        if parent.base_environment_profile is None and profile.adapter_key == LocalEnvdProjectAdapter.key:
            # Legacy captures only retained the admitted profile. Rebuild the original input,
            # never treat the parent's derived grants as explicit grants for the child.
            source = await self._configurations.load(parent.generation_digest)
            base = self._resolver.environment_profile(source, profile.profile_id)
            if base.behavior_digest != profile.behavior_digest:
                raise CompositionError(
                    "The original local Environment profile cannot be reconstructed; start a new parent Run.",
                    code="subagent_environment_incompatible",
                )
            profile = base

        used = {("environment_provider", profile.provider_key), ("environment_adapter", profile.adapter_key)}
        used.update(("environment_run_extension", item.extension_key) for item in parent.environment_run_extensions)
        pending = [root]
        while pending:
            node = pending.pop()
            used.update(("capability", item.capability) for item in node.capabilities)
            used.update(("harness_plugin", item.plugin_key) for item in node.harness_plugins)
            pending.extend(item.definition for item in node.children)
        return ResolvedRunComposition(
            package_prompt_revision=package_prompt_revision or parent.package_prompt_revision,
            generation_digest=parent.generation_digest,
            thread_id=thread_id,
            thread_configuration_version=1,
            root=root,
            project_id=parent.project_id,
            project_roots=parent.project_roots,
            environment_profile=profile,
            environment_bindings=parent.environment_bindings,
            default_environment=parent.default_environment,
            environment_run_extensions=parent.environment_run_extensions,
            dependencies=tuple(item for item in parent.dependencies if (item.kind, item.key) in used),
            run_configuration=parent.run_configuration,
            agent_creation=parent.agent_creation,
            subagents=parent.subagents,
            tool_execution=parent.tool_execution,
            memory_enabled=parent.memory_enabled,
            background_compaction=parent.background_compaction,
            media_understanding=parent.media_understanding,
            web_providers=parent.web_providers,
            content_plugins=parent.content_plugins,
        ).model_copy(deep=True)

    async def _bind_oauth(self, value: ResolvedRunComposition) -> ResolvedRunComposition:
        if self._oauth is None:
            return value
        return value.model_copy(update={"root": await self._oauth.bind_node(value.root)})

    async def save(self, value: ResolvedRunComposition) -> PublishedRunComposition:
        envelope = await self._store.objects.publish_model(object_kind=ObjectKind.run_composition, value=value)
        return PublishedRunComposition(value=value, reference=envelope.ref)


def _resource_value(source: LoadedHarnessUiConfiguration, kind: str, resource_id: str) -> object:
    resources = {
        "model": source.models,
        "web_provider": source.web_providers,
        "subscription_group": source.subscription_groups,
        "harness_plugin": source.harness_plugins,
        "environment_profile": source.environment_profiles,
        "environment_run_extension": source.environment_run_extensions,
        "mcp_server": source.mcp_servers,
        "agent": source.agents,
        "acp_agent": source.acp_agents,
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
