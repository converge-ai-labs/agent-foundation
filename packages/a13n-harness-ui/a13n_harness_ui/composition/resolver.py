"""Resolve accepted resources and one sticky Thread configuration into a Run composition."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from a13n_environment import EnvironmentProviderRegistration
from a13n_harness.environment import (
    EnvironmentRunExtensionFactoryContext,
    EnvironmentRunExtensionFactoryRegistration,
)
from a13n_harness.errors import PluginError
from a13n_harness.plugin_factories import (
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryRegistration,
)
from pydantic import JsonValue

from a13n_harness_ui.configuration import (
    AgentResource,
    AgentSubagentSelection,
    ApiKeyAuthentication,
    CanonicalSubagent,
    CodexSubscriptionAuthentication,
    GrokSubscriptionAuthentication,
    LoadedHarnessUiConfiguration,
    ModelResource,
    canonical_digest,
)
from a13n_harness_ui.configuration.models import MarkdownSubagentSelection
from a13n_harness_ui.environment_profiles import (
    FULL_CONTROL_PROFILE_ID,
    built_in_environment_profile,
)
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog, SelectedCapability
from a13n_harness_ui.model_adapters import PydanticAiModelAdapter
from a13n_harness_ui.prompts import DEFAULT_SYSTEM_PROMPT
from a13n_harness_ui.surfaces import RunModelOverrides

from .models import (
    DependencyProvenance,
    ResolvedAgentNode,
    ResolvedCapabilityRecipe,
    ResolvedContentPlugin,
    ResolvedEnvironmentProfile,
    ResolvedMcpRecipe,
    ResolvedModelRecipe,
    ResolvedPluginRecipe,
    ResolvedRunComposition,
    ResolvedRunExtensionRecipe,
    ResolvedSubagent,
)

PACKAGE_SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT
PACKAGE_PROMPT_REVISION = "a13n-harness-ui/3"
IMPLICIT_NATIVE_PROFILE = FULL_CONTROL_PROFILE_ID
_MAX_RESOLVED_NODES = 1024
_MAX_RESOLVED_DEPTH = 128


@dataclass(frozen=True, slots=True)
class ThreadCompositionSelection:
    thread_id: str
    version: int
    project_id: str
    agent_source_kind: Literal["agent", "markdown"]
    agent_source_id: str
    environment_profile_id: str
    harness_plugin_ids: tuple[str, ...]
    environment_run_extension_ids: tuple[str, ...]
    mcp_server_ids: tuple[str, ...]


class AgentCompositionResolver:
    """Validate installed selections and capture a complete immutable Run value."""

    def __init__(self, catalog: HarnessUiExtensionCatalog | None = None) -> None:
        self.catalog = catalog or HarnessUiExtensionCatalog()
        self._model_adapter = PydanticAiModelAdapter()

    def validate_generation(self, source: LoadedHarnessUiConfiguration) -> None:
        """Validate every configured catalog key and package-owned configuration."""

        plugin_keys = tuple(item.plugin_key for item in source.harness_plugins.values())
        plugin_catalog = self.catalog.plugin_catalog(plugin_keys)
        for item in source.harness_plugins.values():
            try:
                plugin_catalog.validate_configuration(item.plugin_key, item.configuration)
            except PluginError as exc:
                raise CompositionError(
                    "Harness Plugin configuration is invalid.",
                    code="plugin_configuration_invalid",
                    details={"plugin_id": item.id, "plugin_key": item.plugin_key},
                ) from exc

        provider_keys = tuple(item.provider_key for item in source.environment_profiles.values())
        if provider_keys:
            providers = self.catalog.provider_catalog(provider_keys)
            for item in source.environment_profiles.values():
                provider = providers.require(item.provider_key)
                adapter = self.catalog.environment_adapter(item.adapter_key, item.provider_key)
                adapter.validate_profile(
                    provider_schema_version=item.provider_schema_version,
                    provider_configuration=item.provider_configuration,
                    adapter_configuration=item.adapter_configuration,
                    provider=provider,
                )

        extension_keys = tuple(item.extension_key for item in source.environment_run_extensions.values())
        extension_catalog = self.catalog.run_extension_catalog(extension_keys)
        for item in source.environment_run_extensions.values():
            extension_catalog.create_extension(
                EnvironmentRunExtensionFactoryContext(
                    extension_key=item.extension_key,
                    extension_id=item.id,
                    configuration=item.configuration,
                )
            )

        for model in source.models.values():
            self._model_recipe(model)
        for agent in source.agents.values():
            self._capability_recipes(source, agent)

    def resolve_run(
        self,
        source: LoadedHarnessUiConfiguration,
        selection: ThreadCompositionSelection,
        *,
        parent_node: ResolvedAgentNode | None = None,
        model_overrides: RunModelOverrides | None = None,
    ) -> ResolvedRunComposition:
        """Resolve one exact Thread head against one accepted source generation."""

        self._validate_selection(source, selection)
        project = source.projects[selection.project_id]
        plugin_catalog = self.catalog.plugin_catalog(tuple(item.plugin_key for item in source.harness_plugins.values()))
        budget = [_MAX_RESOLVED_NODES]
        if selection.agent_source_kind == "agent":
            root = self._agent_node(
                source,
                source.agents[selection.agent_source_id],
                plugin_catalog=plugin_catalog,
                root_plugins=selection.harness_plugin_ids,
                root_mcp=selection.mcp_server_ids,
                budget=budget,
                depth=1,
                included_subagents=source.document.subagents.include if parent_node is None else (),
                model_overrides=model_overrides,
            )
        else:
            if parent_node is None:
                raise CompositionError(
                    "A Markdown child Run requires its admitting parent composition.",
                    code="markdown_parent_missing",
                )
            root = self._markdown_node(
                source,
                source.subagents[selection.agent_source_id],
                parent=parent_node,
                plugin_ids=selection.harness_plugin_ids,
                mcp_ids=selection.mcp_server_ids,
                budget=budget,
                depth=1,
            )

        environment = self._environment_profile(source, selection.environment_profile_id)
        run_extensions, extension_dependencies = self._run_extensions(source, selection.environment_run_extension_ids)
        dependencies = [
            *self._agent_dependencies(source, root),
            *self._environment_dependencies(source, environment),
            *extension_dependencies,
        ]
        unique = {
            (
                item.kind,
                item.key,
                item.source,
                item.class_module,
                item.class_qualname,
                item.import_target,
                item.distribution_name,
                item.distribution_version,
            ): item
            for item in dependencies
        }
        return ResolvedRunComposition(
            package_prompt_revision=PACKAGE_PROMPT_REVISION,
            generation_digest=source.source_digest,
            thread_id=selection.thread_id,
            thread_configuration_version=selection.version,
            project_id=project.id,
            project_roots=tuple(item.path for item in project.roots),
            content_plugins=tuple(
                ResolvedContentPlugin(
                    plugin_id=item.plugin_id,
                    version=item.version,
                    commit=item.commit,
                    path=item.path,
                    skills_path=item.skills_path,
                )
                for item in sorted(source.content_plugins, key=lambda plugin: plugin.plugin_id)
            ),
            root=root,
            environment_profile=environment,
            environment_run_extensions=run_extensions,
            dependencies=tuple(sorted(unique.values(), key=lambda item: (item.kind, item.key))),
        )

    def _validate_selection(
        self,
        source: LoadedHarnessUiConfiguration,
        selection: ThreadCompositionSelection,
    ) -> None:
        if selection.project_id not in source.projects:
            raise CompositionError("The Thread Project is unavailable.", code="project_missing")
        sources = source.agents if selection.agent_source_kind == "agent" else source.subagents
        if selection.agent_source_id not in sources:
            raise CompositionError("The Thread Agent source is unavailable.", code="agent_source_missing")
        if (
            built_in_environment_profile(selection.environment_profile_id) is None
            and selection.environment_profile_id not in source.environment_profiles
        ):
            raise CompositionError("The Thread Environment profile is unavailable.", code="environment_profile_missing")
        _require_ids(selection.harness_plugin_ids, source.harness_plugins, "Harness Plugin")
        _require_ids(selection.environment_run_extension_ids, source.environment_run_extensions, "Run Extension")
        _require_ids(selection.mcp_server_ids, source.mcp_servers, "MCP server")

    def _agent_node(
        self,
        source: LoadedHarnessUiConfiguration,
        agent: AgentResource,
        *,
        plugin_catalog: HarnessPluginFactoryCatalog,
        root_plugins: tuple[str, ...] | None,
        root_mcp: tuple[str, ...] | None,
        budget: list[int],
        depth: int,
        included_subagents: tuple[str, ...] = (),
        model_overrides: RunModelOverrides | None = None,
    ) -> ResolvedAgentNode:
        _consume_budget(budget, depth)
        plugin_ids = source.selected_plugins(agent) if root_plugins is None else root_plugins
        mcp_ids = source.selected_mcp_servers(agent) if root_mcp is None else root_mcp
        plugins = self._plugins(source, plugin_ids, plugin_catalog)
        mcp = tuple(ResolvedMcpRecipe(server_id=item, transport=source.mcp_servers[item].transport) for item in mcp_ids)
        selected_model = model_overrides.model_id if model_overrides and model_overrides.model_id else agent.model
        if selected_model is None:
            raise CompositionError(
                "This Agent has no model yet. Open Setup to connect a model and select a configured Agent before sending.",
                code="agent_model_required",
            )
        if selected_model not in source.models:
            raise CompositionError("The selected model is unavailable.", code="model_missing")
        resource = source.models[selected_model]
        if model_overrides is not None and model_overrides.thinking is not None:
            resource = resource.model_copy(
                update={"settings": {**resource.settings, "thinking": model_overrides.thinking}}
            )
        model = self._model_recipe(resource)
        capabilities = self._capability_recipes(source, agent, active_model=model)
        children: list[ResolvedSubagent] = []
        provisional = ResolvedAgentNode(
            source_kind="agent",
            source_id=agent.id,
            roster_name=agent.id,
            system_prompt=(PACKAGE_SYSTEM_PROMPT,),
            instructions=(agent.instructions,) if agent.instructions.strip() else (),
            global_guidance=source.global_guidance,
            model=model,
            capabilities=capabilities,
            harness_plugins=plugins,
            mcp_servers=mcp,
            tools=agent.tools,
            children=(),
        )
        edges = list(agent.subagents)
        if included_subagents:
            explicit_markdown = {edge.markdown for edge in edges if isinstance(edge, MarkdownSubagentSelection)}
            edges.extend(
                MarkdownSubagentSelection(markdown=f"subagent-builtin-{name}")
                for name in included_subagents
                if f"subagent-builtin-{name}" not in explicit_markdown
            )
        for edge in edges:
            if isinstance(edge, AgentSubagentSelection):
                child_resource = source.agents[edge.agent]
                child = self._agent_node(
                    source,
                    child_resource,
                    plugin_catalog=plugin_catalog,
                    root_plugins=None,
                    root_mcp=None,
                    budget=budget,
                    depth=depth + 1,
                )
                children.append(
                    ResolvedSubagent(
                        name=child_resource.id,
                        description=f"Delegate suitable work to {child_resource.name}.",
                        source_kind="agent",
                        source_id=child_resource.id,
                        definition=child,
                    )
                )
            else:
                markdown = source.subagents.get(edge.markdown)
                if markdown is None:
                    raise CompositionError(
                        "A selected Markdown subagent is unavailable; repair its source or remove the selection.",
                        code="composition_subagent_unavailable",
                        details={"agent_id": agent.id, "subagent_id": edge.markdown},
                    )
                child = self._markdown_node(
                    source,
                    markdown,
                    parent=provisional,
                    plugin_ids=plugin_ids,
                    mcp_ids=mcp_ids,
                    budget=budget,
                    depth=depth + 1,
                )
                children.append(
                    ResolvedSubagent(
                        name=markdown.name,
                        description=markdown.description,
                        instruction=markdown.instruction,
                        source_kind="markdown",
                        source_id=markdown.id,
                        definition=child,
                    )
                )
        if len({child.name for child in children}) != len(children):
            raise CompositionError(
                "The selected Agent has duplicate roster names.",
                code="composition_subagent_conflict",
                details={"agent_id": agent.id},
            )
        return provisional.model_copy(update={"children": tuple(children)})

    def _markdown_node(
        self,
        source: LoadedHarnessUiConfiguration,
        child: CanonicalSubagent,
        *,
        parent: ResolvedAgentNode,
        plugin_ids: tuple[str, ...],
        mcp_ids: tuple[str, ...],
        budget: list[int],
        depth: int,
    ) -> ResolvedAgentNode:
        _consume_budget(budget, depth)
        # Only retained pre-inheritance generations can carry a Markdown model.
        # New file parsing rejects that field; old admitted children keep their recipe.
        if child.model is not None and child.model not in source.models:
            raise CompositionError(
                "The retained Markdown subagent model is unavailable.",
                code="composition_subagent_model_unavailable",
                details={"subagent_id": child.id, "model_id": child.model},
            )
        model = parent.model if child.model is None else self._model_recipe(source.models[child.model])
        return ResolvedAgentNode(
            source_kind="markdown",
            source_id=child.id,
            roster_name=child.name,
            system_prompt=(PACKAGE_SYSTEM_PROMPT,),
            instructions=(child.body,) if child.body.strip() else (),
            global_guidance=source.global_guidance,
            model=model,
            capabilities=parent.capabilities,
            harness_plugins=self._plugins(
                source,
                plugin_ids,
                self.catalog.plugin_catalog(tuple(item.plugin_key for item in source.harness_plugins.values())),
            ),
            mcp_servers=tuple(
                ResolvedMcpRecipe(server_id=item, transport=source.mcp_servers[item].transport) for item in mcp_ids
            ),
            tools=parent.tools if child.tools is None else child.tools,
            children=(),
        )

    def _model_recipe(self, item: ModelResource) -> ResolvedModelRecipe:
        _validate_auth_route(item)
        normalized = self._model_adapter.validate(
            route=item.route,
            settings=item.settings,
            model_cfg=item.model_configuration,
        )
        return ResolvedModelRecipe(
            model_id=item.id,
            route=normalized.route,
            authentication=item.authentication,
            settings=normalized.settings,
            model_configuration=normalized.model_cfg,
            model_characteristics=item.model_characteristics,
        )

    def _capability_recipes(
        self,
        source: LoadedHarnessUiConfiguration,
        agent: AgentResource,
        *,
        active_model: ResolvedModelRecipe | None = None,
    ) -> tuple[ResolvedCapabilityRecipe, ...]:
        self._capabilities(agent)
        recipes: list[ResolvedCapabilityRecipe] = []
        tools = source.document.tools
        disabled = {
            name
            for name, enabled in (("user_interaction", tools.enable_user_input), ("codeact", tools.enable_codeact))
            if not enabled
        }
        for item in agent.capabilities:
            if item.capability in disabled:
                continue
            configuration = dict(item.configuration)
            characteristics = None if active_model is None else active_model.model_characteristics
            if (
                item.capability == "runtime_context"
                and "context_window_tokens" not in configuration
                and characteristics is not None
                and characteristics.context_window is not None
            ):
                configuration["context_window_tokens"] = characteristics.context_window
            model = None
            if item.capability == "ShellReviewCapability":
                model_id = item.configuration.get("model")
                if not isinstance(model_id, str) or model_id not in source.models:
                    raise CompositionError(
                        "Shell review must reference an available Model resource.",
                        code="capability_model_missing",
                        details={"agent_id": agent.id},
                    )
                model = self._model_recipe(source.models[model_id])
            recipes.append(
                ResolvedCapabilityRecipe(
                    capability=item.capability,
                    configuration=configuration,
                    model=model,
                )
            )
        for default in ("file_context", "working_state", "user_interaction", "codeact"):
            if default not in disabled and not any(item.capability == default for item in recipes):
                recipes.append(
                    ResolvedCapabilityRecipe(
                        capability=default,
                        configuration={"notes_enabled": False} if default == "working_state" else {},
                    )
                )
        return tuple(recipes)

    def _capabilities(self, agent: AgentResource) -> tuple[SelectedCapability, ...]:
        return self.catalog.capabilities(tuple((item.capability, item.configuration) for item in agent.capabilities))

    def _plugins(
        self,
        source: LoadedHarnessUiConfiguration,
        plugin_ids: tuple[str, ...],
        plugin_catalog: HarnessPluginFactoryCatalog,
    ) -> tuple[ResolvedPluginRecipe, ...]:
        result: list[ResolvedPluginRecipe] = []
        for resource_id in plugin_ids:
            resource = source.harness_plugins[resource_id]
            try:
                normalized = plugin_catalog.validate_configuration(resource.plugin_key, resource.configuration)
            except PluginError as exc:
                raise CompositionError(
                    "Harness Plugin configuration is invalid.", code="plugin_configuration_invalid"
                ) from exc
            result.append(
                ResolvedPluginRecipe(
                    plugin_id=resource.id,
                    plugin_key=resource.plugin_key,
                    configuration=dict(normalized),
                )
            )
        return tuple(result)

    def _environment_profile(
        self,
        source: LoadedHarnessUiConfiguration,
        profile_id: str,
    ) -> ResolvedEnvironmentProfile:
        built_in = built_in_environment_profile(profile_id)
        if built_in is not None:
            behavior = {
                "provider_key": built_in.provider_key,
                "provider_schema_version": built_in.provider_schema_version,
                "provider_configuration": {},
                "adapter_key": built_in.adapter_key,
                "adapter_configuration": {},
            }
            return ResolvedEnvironmentProfile(
                profile_id=profile_id,
                behavior_digest=canonical_digest(behavior),
                provider_key=built_in.provider_key,
                provider_schema_version=built_in.provider_schema_version,
                provider_configuration={},
                adapter_key=built_in.adapter_key,
                adapter_configuration={},
            )
        item = source.environment_profiles[profile_id]
        provider = self.catalog.provider_catalog((item.provider_key,)).require(item.provider_key)
        adapter = self.catalog.environment_adapter(item.adapter_key, item.provider_key)
        provider_configuration, adapter_configuration = adapter.validate_profile(
            provider_schema_version=item.provider_schema_version,
            provider_configuration=item.provider_configuration,
            adapter_configuration=item.adapter_configuration,
            provider=provider,
        )
        behavior: dict[str, JsonValue] = {
            "provider_key": item.provider_key,
            "provider_schema_version": item.provider_schema_version,
            "provider_configuration": provider_configuration,
            "adapter_key": item.adapter_key,
            "adapter_configuration": adapter_configuration,
        }
        return ResolvedEnvironmentProfile(
            profile_id=item.id,
            behavior_digest=canonical_digest(behavior),
            provider_key=item.provider_key,
            provider_schema_version=item.provider_schema_version,
            provider_configuration=provider_configuration,
            adapter_key=item.adapter_key,
            adapter_configuration=adapter_configuration,
        )

    def _run_extensions(
        self,
        source: LoadedHarnessUiConfiguration,
        resource_ids: tuple[str, ...],
    ) -> tuple[tuple[ResolvedRunExtensionRecipe, ...], tuple[DependencyProvenance, ...]]:
        resources = tuple(source.environment_run_extensions[item] for item in resource_ids)
        catalog = self.catalog.run_extension_catalog(tuple(item.extension_key for item in resources))
        registrations = {item.extension_key: item for item in catalog.registrations}
        recipes: list[ResolvedRunExtensionRecipe] = []
        dependencies: list[DependencyProvenance] = []
        for item in resources:
            catalog.create_extension(
                EnvironmentRunExtensionFactoryContext(
                    extension_key=item.extension_key,
                    extension_id=item.id,
                    configuration=item.configuration,
                )
            )
            registration = registrations[item.extension_key]
            recipes.append(
                ResolvedRunExtensionRecipe(
                    extension_id=item.id,
                    extension_key=item.extension_key,
                    configuration=item.configuration,
                )
            )
            dependencies.append(
                _registration_provenance(
                    "environment_run_extension",
                    registration,
                    source=self.catalog.run_extension_source(item.extension_key),
                )
            )
        return tuple(recipes), tuple(dependencies)

    def _agent_dependencies(
        self,
        source: LoadedHarnessUiConfiguration,
        root: ResolvedAgentNode,
    ) -> tuple[DependencyProvenance, ...]:
        nodes = [root]
        plugin_keys: set[str] = set()
        capability_keys: set[str] = set()
        while nodes:
            node = nodes.pop()
            plugin_keys.update(item.plugin_key for item in node.harness_plugins)
            capability_keys.update(item.capability for item in node.capabilities)
            nodes.extend(item.definition for item in node.children)
        dependencies: list[DependencyProvenance] = []
        if plugin_keys:
            catalog = self.catalog.plugin_catalog(tuple(sorted(plugin_keys)))
            registrations = {item.plugin_key: item for item in catalog.registrations}
            dependencies.extend(
                _registration_provenance(
                    "harness_plugin",
                    registrations[key],
                    source=self.catalog.plugin_source(key),
                )
                for key in sorted(plugin_keys)
            )
        for key in sorted(capability_keys):
            selected = self.catalog.capabilities(((key, self._capability_configuration(root, key)),))[0]
            reference = selected.implementation
            dependencies.append(
                DependencyProvenance(
                    kind="capability",
                    key=key,
                    source=reference.source,
                    class_module=reference.implementation_type.__module__,
                    class_qualname=reference.implementation_type.__qualname__,
                    import_target=reference.import_target,
                    distribution_name=reference.distribution_name,
                    distribution_version=reference.distribution_version,
                )
            )
        return tuple(dependencies)

    def _capability_configuration(self, root: ResolvedAgentNode, key: str) -> dict[str, JsonValue]:
        stack = [root]
        while stack:
            node = stack.pop()
            for item in node.capabilities:
                if item.capability == key:
                    return item.configuration
            stack.extend(item.definition for item in node.children)
        raise KeyError(key)

    def _environment_dependencies(
        self,
        source: LoadedHarnessUiConfiguration,
        profile: ResolvedEnvironmentProfile,
    ) -> tuple[DependencyProvenance, ...]:
        provider = self.catalog.provider_catalog((profile.provider_key,))
        registration = next(item for item in provider.registrations if item.provider_key == profile.provider_key)
        adapter = self.catalog.adapter_reference(profile.adapter_key)
        return (
            _registration_provenance(
                "environment_provider",
                registration,
                source=self.catalog.provider_source(profile.provider_key),
            ),
            DependencyProvenance(
                kind="environment_adapter",
                key=profile.adapter_key,
                source=adapter.source,
                class_module=adapter.implementation_type.__module__,
                class_qualname=adapter.implementation_type.__qualname__,
                import_target=adapter.import_target,
                distribution_name=adapter.distribution_name,
                distribution_version=adapter.distribution_version,
            ),
        )


def _registration_provenance(
    kind: Literal["harness_plugin", "environment_provider", "environment_run_extension"],
    registration: HarnessPluginFactoryRegistration
    | EnvironmentProviderRegistration
    | EnvironmentRunExtensionFactoryRegistration,
    *,
    source: Literal["installed", "host"],
) -> DependencyProvenance:
    if isinstance(registration, HarnessPluginFactoryRegistration):
        key = registration.plugin_key
    elif isinstance(registration, EnvironmentProviderRegistration):
        key = registration.provider_key
    else:
        key = registration.extension_key
    return DependencyProvenance(
        kind=kind,
        key=key,
        source=source,
        class_module=registration.class_module,
        class_qualname=registration.class_qualname,
        import_target=registration.import_target,
        distribution_name=registration.distribution_name,
        distribution_version=registration.distribution_version,
    )


def _validate_auth_route(item: ModelResource) -> None:
    prefix = item.route.split(":", 1)[0]
    authentication = item.authentication
    if isinstance(authentication, CodexSubscriptionAuthentication) and prefix != "openai-codex":
        raise CompositionError(
            "Codex subscription authentication requires an openai-codex route.", code="model_auth_invalid"
        )
    if isinstance(authentication, GrokSubscriptionAuthentication) and prefix not in {"grok", "grok-build"}:
        raise CompositionError("Grok subscription authentication requires a Grok route.", code="model_auth_invalid")
    if isinstance(authentication, ApiKeyAuthentication) and prefix in {"openai-codex", "grok-build"}:
        raise CompositionError(
            "The selected subscription route does not accept API-key authentication.", code="model_auth_invalid"
        )


def _consume_budget(budget: list[int], depth: int) -> None:
    budget[0] -= 1
    if budget[0] < 0 or depth > _MAX_RESOLVED_DEPTH:
        raise CompositionError("Resolved Agent graph exceeds its bounds.", code="agent_graph_too_large")


def _require_ids(values: tuple[str, ...], resources: Mapping[str, object], label: str) -> None:
    if len(values) != len(set(values)):
        raise CompositionError(f"{label} selections must be unique.", code="thread_configuration_invalid")
    for value in values:
        if value not in resources:
            raise CompositionError(f"The selected {label} is unavailable.", code="thread_resource_missing")


__all__ = [
    "IMPLICIT_NATIVE_PROFILE",
    "PACKAGE_PROMPT_REVISION",
    "PACKAGE_SYSTEM_PROMPT",
    "AgentCompositionResolver",
    "ThreadCompositionSelection",
]
