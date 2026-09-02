"""Resolve one graph-valid Agent UI source candidate into trusted immutable snapshots."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from a13n_environment_provider import EnvironmentProvider
from a13n_harness import __version__ as harness_version
from a13n_harness.errors import PluginError
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog
from pydantic import BaseModel, JsonValue

from a13n_ui.configuration.models import (
    AgentConfig,
    AgentSubagentSelection,
    CanonicalSubagent,
    LoadedAgentUiConfiguration,
    MarkdownSubagentSelection,
    ModelConfig,
)
from a13n_ui.errors import CompositionError
from a13n_ui.model_adapters import PydanticAiModelAdapter

from .catalogs import (
    LOCAL_EIP_BINDER_KEY,
    LOCAL_EIP_PROVIDER_KEY,
    NATIVE_BINDER_KEY,
    NATIVE_PROVIDER_KEY,
    BinderCatalogEntry,
    ProviderCatalogEntry,
    TrustedProviderCatalog,
    WorkspaceBinderCatalog,
    builtin_workspace_binder_catalog,
    mcp_adapter_lock,
    plugin_dependency_lock,
    pydantic_ai_adapter_lock,
    selected_plugin_catalog,
    selected_provider_catalog,
)
from .models import (
    DependencyLock,
    ResolvedAgentNode,
    ResolvedAgentSnapshot,
    ResolvedEnvironmentProfile,
    ResolvedMcpRecipe,
    ResolvedModelRecipe,
    ResolvedPluginRecipe,
    ResolvedSubagent,
    dependency_sort_key,
    resolved_agent_node,
)

PACKAGE_PROMPT_REVISION = "1"
PACKAGE_SYSTEM_PROMPT = "You are an AI assistant running in Agent UI."
IMPLICIT_NATIVE_PROFILE = "__native__"
_MAX_RESOLVED_NODES = 1024


@dataclass(frozen=True, slots=True)
class ResolvedConfiguration:
    """Complete snapshot set produced from one stable source candidate."""

    source: LoadedAgentUiConfiguration
    agents: Mapping[str, ResolvedAgentSnapshot]
    environments: Mapping[str, ResolvedEnvironmentProfile]
    default_agent: str | None
    default_environment: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "agents", MappingProxyType(dict(self.agents)))
        object.__setattr__(self, "environments", MappingProxyType(dict(self.environments)))


class AgentCompositionResolver:
    """Trusted all-or-nothing resolution boundary for Agent UI composition."""

    def __init__(
        self,
        *,
        plugin_catalog: HarnessPluginFactoryCatalog | None = None,
        binder_catalog: WorkspaceBinderCatalog | None = None,
        provider_entries: tuple[ProviderCatalogEntry, ...] = (),
    ) -> None:
        self._injected_plugin_catalog = plugin_catalog
        self._binders = binder_catalog or builtin_workspace_binder_catalog()
        self._provider_entries = provider_entries
        self._model_adapter = PydanticAiModelAdapter()
        self._model_lock = pydantic_ai_adapter_lock()
        self._mcp_lock = mcp_adapter_lock()

    def resolve(self, source: LoadedAgentUiConfiguration) -> ResolvedConfiguration:
        """Resolve every configured Agent and Environment profile without native construction or I/O."""

        document = source.document
        models = {name: self._model_recipe(item) for name, item in sorted(document.models.items())}
        plugin_recipes = self._plugin_recipes(source)
        mcp_recipes = {
            name: ResolvedMcpRecipe(
                server_name=name,
                transport=item.transport,
                adapter_lock=self._mcp_lock,
            )
            for name, item in sorted(document.mcp_servers.items())
            if item.enabled
        }

        _require_resolvable_graph(document.agents)
        agents: dict[str, ResolvedAgentSnapshot] = {}
        for name in sorted(document.agents):
            budget = [_MAX_RESOLVED_NODES]
            root = self._agent_node(
                source=source,
                name=name,
                models=models,
                plugins=plugin_recipes,
                mcp_servers=mcp_recipes,
                budget=budget,
            )
            dependencies = tuple(sorted(_node_dependencies(root), key=dependency_sort_key))
            agents[name] = ResolvedAgentSnapshot(
                harness_release=harness_version,
                package_prompt_revision=PACKAGE_PROMPT_REVISION,
                package_prompt_digest=_prompt_digest(),
                dependencies=dependencies,
                root=root,
            )

        environments = self._environment_profiles(source)
        if IMPLICIT_NATIVE_PROFILE not in environments:
            environments[IMPLICIT_NATIVE_PROFILE] = self._builtin_environment(
                profile_name=IMPLICIT_NATIVE_PROFILE,
                kind="native",
                profile_configuration={},
            )
        default_environment = document.defaults.environment or IMPLICIT_NATIVE_PROFILE
        return ResolvedConfiguration(
            source=source,
            agents=agents,
            environments=environments,
            default_agent=document.defaults.agent,
            default_environment=default_environment,
        )

    def _model_recipe(self, item: ModelConfig) -> ResolvedModelRecipe:
        normalized = self._model_adapter.validate(
            route=item.model,
            settings=item.settings,
            model_cfg=item.model_cfg,
        )
        return ResolvedModelRecipe(
            adapter_key=self._model_adapter.key,
            route=normalized.route,
            api_key=item.api_key,
            settings=normalized.settings,
            model_cfg=normalized.model_cfg,
            adapter_lock=self._model_lock,
        )

    def _overridden_model(
        self,
        *,
        source: LoadedAgentUiConfiguration,
        child: CanonicalSubagent,
        inherited: ResolvedModelRecipe,
    ) -> ResolvedModelRecipe:
        if child.model is None:
            route = inherited.route
            api_key = inherited.api_key
            base_settings = inherited.settings
            base_model_cfg = inherited.model_cfg
        else:
            selected = source.document.models[child.model]
            route = selected.model
            api_key = selected.api_key
            base_settings = selected.settings
            base_model_cfg = selected.model_cfg
        settings = dict(base_settings)
        if child.model_settings is not None:
            settings.update(child.model_settings)
        model_cfg = dict(base_model_cfg)
        if child.model_cfg is not None:
            model_cfg.update(child.model_cfg)
        normalized = self._model_adapter.validate(route=route, settings=settings, model_cfg=model_cfg)
        return ResolvedModelRecipe(
            adapter_key=self._model_adapter.key,
            route=normalized.route,
            api_key=api_key,
            settings=normalized.settings,
            model_cfg=normalized.model_cfg,
            adapter_lock=self._model_lock,
        )

    def _plugin_recipes(self, source: LoadedAgentUiConfiguration) -> dict[str, ResolvedPluginRecipe]:
        enabled = {name: item for name, item in source.document.plugins.items() if item.enabled}
        keys = tuple(sorted({item.plugin for item in enabled.values()}))
        catalog = selected_plugin_catalog(keys, catalog=self._injected_plugin_catalog)
        registrations = {item.plugin_key: item for item in catalog.registrations}
        result: dict[str, ResolvedPluginRecipe] = {}
        for name, item in sorted(enabled.items()):
            registration = registrations.get(item.plugin)
            if registration is None:
                raise CompositionError(
                    "A selected Harness Plugin registration is unavailable.",
                    code="plugin_catalog_invalid",
                    details={"plugin_key": item.plugin},
                )
            try:
                normalized = catalog.validate_configuration(item.plugin, item.configuration)
            except PluginError as exc:
                raise CompositionError(
                    "Harness Plugin configuration is invalid.",
                    code="plugin_configuration_invalid",
                    details={"plugin_name": name, "plugin_key": item.plugin},
                ) from exc
            result[name] = ResolvedPluginRecipe(
                instance_name=name,
                plugin_key=item.plugin,
                configuration=dict(normalized),
                factory_lock=plugin_dependency_lock(registration),
            )
        return result

    def _agent_node(
        self,
        *,
        source: LoadedAgentUiConfiguration,
        name: str,
        models: Mapping[str, ResolvedModelRecipe],
        plugins: Mapping[str, ResolvedPluginRecipe],
        mcp_servers: Mapping[str, ResolvedMcpRecipe],
        budget: list[int],
    ) -> ResolvedAgentNode:
        _consume_node_budget(budget)
        definition = source.document.agents[name]
        selected_plugins = tuple(plugins[item] for item in source.document.selected_plugins(definition))
        selected_mcp = tuple(mcp_servers[item] for item in source.document.selected_mcp_servers(definition))
        instructions = (PACKAGE_SYSTEM_PROMPT, definition.instructions)
        children: list[ResolvedSubagent] = []
        for selection in definition.subagents:
            if isinstance(selection, MarkdownSubagentSelection):
                markdown = source.markdown(selection.markdown)
                children.append(
                    self._markdown_child(
                        source=source,
                        child=markdown,
                        inherited_model=models[definition.model],
                        inherited_instructions=instructions,
                        inherited_plugins=selected_plugins,
                        inherited_mcp=selected_mcp,
                        budget=budget,
                    )
                )
            elif isinstance(selection, AgentSubagentSelection):
                child = self._agent_node(
                    source=source,
                    name=selection.agent,
                    models=models,
                    plugins=plugins,
                    mcp_servers=mcp_servers,
                    budget=budget,
                )
                children.append(
                    ResolvedSubagent(
                        name=selection.agent,
                        description=f"Delegate suitable work to the reusable {selection.agent} Agent.",
                        definition=child,
                    )
                )
            else:  # pragma: no cover - strict source models make this unreachable
                raise TypeError("unsupported subagent selection")
        return resolved_agent_node(
            source_kind="agent",
            source_name=name,
            instructions=instructions,
            model=models[definition.model],
            plugins=selected_plugins,
            mcp_servers=selected_mcp,
            children=tuple(children),
        )

    def _markdown_child(
        self,
        *,
        source: LoadedAgentUiConfiguration,
        child: CanonicalSubagent,
        inherited_model: ResolvedModelRecipe,
        inherited_instructions: tuple[str, ...],
        inherited_plugins: tuple[ResolvedPluginRecipe, ...],
        inherited_mcp: tuple[ResolvedMcpRecipe, ...],
        budget: list[int],
    ) -> ResolvedSubagent:
        _consume_node_budget(budget)
        instructions = inherited_instructions
        if child.body:
            instructions = (*instructions, child.body)
        definition = resolved_agent_node(
            source_kind="markdown",
            source_name=child.name,
            instructions=instructions,
            model=self._overridden_model(source=source, child=child, inherited=inherited_model),
            plugins=inherited_plugins,
            mcp_servers=inherited_mcp,
            tools=child.tools,
            optional_tools=child.optional_tools,
        )
        return ResolvedSubagent(
            name=child.name,
            description=child.description,
            instruction=child.instruction,
            definition=definition,
        )

    def _environment_profiles(self, source: LoadedAgentUiConfiguration) -> dict[str, ResolvedEnvironmentProfile]:
        document = source.document
        requested_keys = {NATIVE_PROVIDER_KEY, LOCAL_EIP_PROVIDER_KEY}
        requested_keys.update(item.provider for item in document.environment_providers.values() if item.enabled)
        providers = selected_provider_catalog(
            provider_keys=sorted(requested_keys),
            explicit_entries=self._provider_entries,
        )

        configured: dict[str, tuple[ProviderCatalogEntry, BinderCatalogEntry, Mapping[str, JsonValue]]] = {}
        for name, item in sorted(document.environment_providers.items()):
            if not item.enabled:
                continue
            provider = providers.require(item.provider)
            binder = self._binders.require(item.binder)
            self._require_compatible_binder(provider.provider, binder)
            self._validate_profile_configuration(
                binder=binder,
                provider_configuration=item.configuration,
                profile_configuration={},
            )
            configured[name] = (provider, binder, item.configuration)

        result: dict[str, ResolvedEnvironmentProfile] = {}
        for name, profile in sorted(document.environments.items()):
            if profile.kind in {"native", "local_eip"}:
                result[name] = self._builtin_environment(
                    profile_name=name,
                    kind=profile.kind,
                    profile_configuration=profile.configuration,
                    providers=providers,
                )
                continue
            selected_name = profile.provider
            if selected_name is None or selected_name not in configured:
                raise CompositionError(
                    "The Environment profile selects an unavailable Provider configuration.",
                    code="environment_provider_missing",
                    details={"profile_name": name},
                )
            provider, binder, provider_configuration = configured[selected_name]
            normalized = self._validate_profile_configuration(
                binder=binder,
                provider_configuration=provider_configuration,
                profile_configuration=profile.configuration,
            )
            result[name] = ResolvedEnvironmentProfile(
                profile_name=name,
                kind="provider",
                provider_key=provider.provider.key,
                provider_schema_version=binder.binder.provider_schema_version,
                binder_key=binder.binder.key,
                configuration=normalized,
                provider_lock=provider.lock,
                binder_lock=binder.lock,
            )
        return result

    def _builtin_environment(
        self,
        *,
        profile_name: str,
        kind: str,
        profile_configuration: Mapping[str, JsonValue],
        providers: TrustedProviderCatalog | None = None,
    ) -> ResolvedEnvironmentProfile:
        if kind == "native":
            provider_key = NATIVE_PROVIDER_KEY
            binder_key = NATIVE_BINDER_KEY
        elif kind == "local_eip":
            provider_key = LOCAL_EIP_PROVIDER_KEY
            binder_key = LOCAL_EIP_BINDER_KEY
        else:  # pragma: no cover - caller is closed over the two built-ins
            raise TypeError("unsupported built-in Environment kind")
        catalog = providers or selected_provider_catalog(
            provider_keys=(NATIVE_PROVIDER_KEY, LOCAL_EIP_PROVIDER_KEY),
            explicit_entries=self._provider_entries,
        )
        provider = catalog.require(provider_key)
        binder = self._binders.require(binder_key)
        self._require_compatible_binder(provider.provider, binder)
        normalized = self._validate_profile_configuration(
            binder=binder,
            provider_configuration={},
            profile_configuration=profile_configuration,
        )
        return ResolvedEnvironmentProfile(
            profile_name=profile_name,
            kind=kind,
            provider_key=provider_key,
            provider_schema_version=binder.binder.provider_schema_version,
            binder_key=binder_key,
            configuration=normalized,
            provider_lock=provider.lock,
            binder_lock=binder.lock,
        )

    @staticmethod
    def _require_compatible_binder(provider: EnvironmentProvider, binder: BinderCatalogEntry) -> None:
        if binder.binder.provider_key != provider.key:
            raise CompositionError(
                "The selected workspace binder is incompatible with its Environment Provider.",
                code="workspace_binder_incompatible",
                details={"binder_key": binder.binder.key, "provider_key": provider.key},
            )
        if binder.binder.provider_schema_version not in provider.configuration_versions:
            raise CompositionError(
                "The workspace binder selects an unsupported Provider configuration version.",
                code="workspace_binder_incompatible",
                details={"binder_key": binder.binder.key, "provider_key": provider.key},
            )

    @staticmethod
    def _validate_profile_configuration(
        *,
        binder: BinderCatalogEntry,
        provider_configuration: Mapping[str, JsonValue],
        profile_configuration: Mapping[str, JsonValue],
    ) -> dict[str, JsonValue]:
        try:
            validated = binder.binder.validate_profile(
                provider_configuration=provider_configuration,
                profile_configuration=profile_configuration,
            )
            if not isinstance(validated, BaseModel):
                raise TypeError("workspace binder validation must return a BaseModel")
            dumped = validated.model_dump(mode="json")
            if not isinstance(dumped, dict) or any(not isinstance(key, str) for key in dumped):
                raise TypeError("workspace binder validation must return an object model")
            return dumped
        except CompositionError:
            raise
        except Exception as exc:
            raise CompositionError(
                "Environment profile configuration is invalid.",
                code="environment_profile_configuration_invalid",
                details={"binder_key": binder.binder.key},
            ) from exc


def _require_resolvable_graph(agents: Mapping[str, AgentConfig]) -> None:
    """Bound every expanded named-Agent tree before recursive snapshot construction."""

    for root in agents:
        count = 0
        stack: list[tuple[str, int]] = [(root, 1)]
        while stack:
            name, depth = stack.pop()
            count += 1
            if count > _MAX_RESOLVED_NODES or depth > 128:
                raise CompositionError(
                    "The resolved Agent graph exceeds the supported size or depth bound.",
                    code="agent_graph_too_large",
                    details={"agent_name": root},
                )
            stack.extend(
                (selection.agent, depth + 1)
                for selection in reversed(agents[name].subagents)
                if isinstance(selection, AgentSubagentSelection)
            )


def _consume_node_budget(budget: list[int]) -> None:
    budget[0] -= 1
    if budget[0] < 0:
        raise CompositionError(
            "The resolved Agent graph exceeds the supported node bound.",
            code="agent_graph_too_large",
        )


def _node_dependencies(root: ResolvedAgentNode) -> set[DependencyLock]:
    result: set[DependencyLock] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        result.add(node.model.adapter_lock)
        result.update(item.factory_lock for item in node.plugins)
        result.update(item.adapter_lock for item in node.mcp_servers)
        stack.extend(child.definition for child in reversed(node.children))
    return result


def _prompt_digest() -> str:
    return hashlib.sha256(PACKAGE_SYSTEM_PROMPT.encode("utf-8")).hexdigest()


__all__ = [
    "IMPLICIT_NATIVE_PROFILE",
    "PACKAGE_PROMPT_REVISION",
    "PACKAGE_SYSTEM_PROMPT",
    "AgentCompositionResolver",
    "ResolvedConfiguration",
]
