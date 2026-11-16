"""Fresh native Harness construction from one resolved Harness UI Run composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentSpec,
    ExecutableAgent,
    HarnessBuilder,
    HarnessInstrumentation,
    ModelRecoveryPolicy,
    RunConfiguration,
    SubagentDefinition,
)
from a13n_harness.capabilities import (
    CompactionCapability,
    SubagentCapability,
    ToolExecutionCapability,
    ToolSearchPlan,
    ToolSearchSelection,
)
from a13n_harness.capabilities.memory import FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.environment.models import FILE_EXECUTION_ACTIONS
from a13n_harness.errors import HarnessError, PluginError
from a13n_harness.mcp import UnavailableMCPToolset
from a13n_harness.model_context import (
    AbstractModelContextCapability,
    ModelContextBlock,
    ModelContextNext,
    ModelContextPlacement,
    ModelContextProjection,
    ModelContextProjectionRequest,
    ModelContextRequestKind,
)
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog, HarnessPluginFactoryContext
from a13n_harness.pricing import PricingCatalog
from a13n_harness.recovery import DEFAULT_RECOVERY_PROMPT
from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY, ToolPermissionsCapability
from a13n_harness.tools.metadata import normalize_harness_tool_metadata
from a13n_harness.tools.tool_search import grouped_tool_name
from a13n_harness.toolsets.computer import ComputerToolset
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from a13n_harness.toolsets.files import FileToolset
from a13n_harness.toolsets.shell import ShellToolset
from pydantic_ai import RunContext, ToolOutput
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import TextContent
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset
from pydantic_ai.usage import UsageLimits

from a13n_harness_ui.compaction import BackgroundCompaction
from a13n_harness_ui.compaction_model import CompactionRun
from a13n_harness_ui.environment_paths import EnvironmentPathLayout
from a13n_harness_ui.environment_profiles import FULL_CONTROL_PROFILE
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.guardian_credits import GuardianParentCapability, GuardianReviewCapability
from a13n_harness_ui.mcp_adapters import HarnessUiMCP
from a13n_harness_ui.mcp_apps.connections import Connections
from a13n_harness_ui.mcp_oauth.service import McpOAuth
from a13n_harness_ui.mcp_runtime.connections import Connections as HostConnections
from a13n_harness_ui.media_understanding import FileMediaUnderstanding
from a13n_harness_ui.memory import MemoryOrganizationRun, bind_memory
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore
from a13n_harness_ui.model_context import CurrentAgentContextCapability
from a13n_harness_ui.model_runtime import HarnessUiModelResolver, SubscriptionSource, model_recipe_id

if TYPE_CHECKING:
    from a13n_harness_ui.subagent_operator import HarnessUiSubagentOperator

from .auxiliary import capability_configuration
from .models import ResolvedAgentNode, ResolvedModelRecipe, ResolvedRunComposition


@dataclass(frozen=True, slots=True)
class ReconstructedAgent:
    """One fresh executable and the Run bindings needed to invoke it."""

    executable: ExecutableAgent[str | None]
    model_resolver: HarnessUiModelResolver
    definition_capability_ids: frozenset[str]
    run_configuration: RunConfiguration = field(default_factory=RunConfiguration)
    media_models: Mapping[NativeInputMediaKind, ResolvedModelRecipe] = field(default_factory=dict)
    memory_cursors: MemoryCursors = field(default_factory=MemoryCursors)
    compaction: CompactionRun | None = None

    def file_media_understanding(self, thread_id: str) -> FileMediaUnderstanding | None:
        if not self.media_models:
            return None
        return FileMediaUnderstanding(self.media_models, self.model_resolver.fresh(), thread_id=thread_id)


def _finish_bot_silently() -> None:
    """Finish this Bot turn without adding a final user-facing reply."""


class _GlobalGuidanceCapability(AbstractModelContextCapability):
    """Project captured global AGENTS text at user role, never as instructions."""

    id = "a13n-harness-ui.global-guidance"

    def __init__(self, sections: tuple[str, ...]) -> None:
        self.sections = sections

    async def wrap_model_context(
        self, ctx: RunContext[AgentContext], request: ModelContextProjectionRequest, handler: ModelContextNext
    ) -> ModelContextProjection:
        projection = await handler(request)
        if request.kind is not ModelContextRequestKind.INPUT:
            return projection
        current = (
            "These are the current global AGENTS.md instructions; they replace earlier global guidance blocks.\n\n"
            + "\n\n".join(self.sections)
            if self.sections
            else "No global AGENTS.md instructions are configured for this run. Earlier global guidance blocks no longer apply."
        )
        content = "# AGENTS.md instructions (global configuration)\n\n<INSTRUCTIONS>\n" + current + "\n</INSTRUCTIONS>"
        block = ModelContextBlock(
            source_id="a13n-harness-ui.global-guidance",
            context_group="environment_context",
            placement=ModelContextPlacement.REQUEST_EPILOGUE,
            content=content,
        )
        if request.history.matches_latest(block):
            return projection
        return ModelContextProjection(
            blocks=(
                block,
                *projection.blocks,
            )
        )


class _ToolAllowlistCapability(AbstractCapability[AgentContext]):
    id: str | None = "a13n.ui.tool-allowlist"

    def __init__(
        self,
        names: frozenset[str],
        *,
        optional_controls: frozenset[str] = frozenset(),
        mcp_groups: Mapping[str, str] | None = None,
    ) -> None:
        self.names = names
        self.optional_controls = optional_controls
        self.mcp_groups = dict(mcp_groups or {})

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return _ToolAllowlistToolset(toolset, self.names, self.optional_controls, self.mcp_groups)


@dataclass
class _ToolAllowlistToolset(WrapperToolset[AgentContext]):
    names: frozenset[str]
    optional_controls: frozenset[str] = frozenset()
    mcp_groups: Mapping[str, str] = field(default_factory=dict)

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        missing = self.names - tools.keys() - self.optional_controls

        def omit_unavailable(toolset: AbstractToolset[AgentContext]) -> None:
            nonlocal missing
            if isinstance(toolset, UnavailableMCPToolset):
                # An unrestricted missing inventory cannot establish whether a
                # name is unknown. Defer that check until a healthy Run; the
                # returned callable surface still intersects the exact allowlist.
                group = self.mcp_groups.get(toolset.id) if toolset.id is not None else None
                if toolset.selected_tools is not None:
                    missing -= {
                        grouped_tool_name(group, name) if group is not None else name for name in toolset.selected_tools
                    }
                elif group is not None:
                    missing = {name for name in missing if not name.startswith(grouped_tool_name(group, ""))}
                else:
                    missing = set()

        if missing:
            self.wrapped.apply(omit_unavailable)
        if missing:
            raise CompositionError(
                "The selected tool allowlist contains an unavailable tool.",
                code="tool_selection_missing",
                details={"tool": sorted(missing)[0]},
            )
        return {name: tool for name, tool in tools.items() if name in self.names}


class _NativeDefaultToolsCapability(AbstractCapability[AgentContext]):
    """Let Harness resolve Shell supersession for the UI's native default surface."""

    id = "a13n.ui.native-default-tools"

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return _NativeDefaultToolsToolset(toolset)


class _NativeDefaultToolsToolset(WrapperToolset[AgentContext]):
    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        mutations = {"filesystem.mkdir", "filesystem.remove", "filesystem.copy", "filesystem.move"}
        result = {}
        for name, tool in tools.items():
            values = tool.tool_def.metadata or {}
            raw = values.get(HARNESS_TOOL_METADATA_KEY)
            if raw is not None:
                metadata = normalize_harness_tool_metadata(raw)
                if metadata.tool_id in mutations:
                    metadata = replace(
                        metadata,
                        superseded_by_tool_ids=metadata.superseded_by_tool_ids | {"environment.shell_exec"},
                    )
                    tool = replace(
                        tool,
                        tool_def=replace(tool.tool_def, metadata={**values, HARNESS_TOOL_METADATA_KEY: metadata}),
                    )
            result[name] = tool
        return result


class AgentReconstructor:
    """Build a fresh native Agent graph without resolving credentials or entering Environments."""

    def __init__(
        self,
        catalog: HarnessUiExtensionCatalog | None = None,
        *,
        user_skills_root: Path | None = None,
        api_keys: ApiKeyStore | None = None,
        configuration_root: Path | None = None,
        instrumentation: HarnessInstrumentation | Literal["environment"] | None = "environment",
        mcp_apps: Connections | None = None,
        mcp_connections: HostConnections | None = None,
        mcp_oauth: McpOAuth | None = None,
    ) -> None:
        self._instrumentation: HarnessInstrumentation | Literal["environment"] | None = instrumentation
        self._mcp_apps = mcp_apps
        self._mcp_oauth = mcp_oauth
        self._mcp_connections = mcp_connections or mcp_apps
        self._api_keys = api_keys
        self._configuration_root = configuration_root
        self._catalog = catalog or HarnessUiExtensionCatalog()
        self._user_skills_root = user_skills_root

    def reconstruct(
        self,
        composition: ResolvedRunComposition,
        *,
        subagent_operator: HarnessUiSubagentOperator | None,
        subagent: bool = False,
        root_capabilities: Sequence[AbstractCapability[Any]] = (),
        subscription_sources: Mapping[str, SubscriptionSource] | None = None,
        pricing_catalog: PricingCatalog | None = None,
        memory_positions: Mapping[str, str | None] | None = None,
        organization: MemoryOrganizationRun | None = None,
        background_compaction: BackgroundCompaction | None = None,
    ) -> ReconstructedAgent:
        if composition.memory_organization:
            if organization is None:
                raise CompositionError(
                    "Memory organization requires a new scope admission.", code="memory_admission_required"
                )
            return self._reconstruct_memory(
                composition, organization, root_capabilities, subscription_sources, pricing_catalog
            )
        memory, cursors = bind_memory(
            self._configuration_root,
            enabled=composition.memory_enabled,
            project_id=composition.project_id,
            positions=memory_positions,
            bot=composition.root.source_kind == "bot",
        )
        from a13n_harness_ui.subagent_tools import SubagentTools, guidance

        dynamic = not subagent and composition.subagents.dynamic.enabled
        has_children = bool(composition.root.children) or dynamic
        subagent_capability = None
        if has_children:
            if subagent_operator is None:
                raise CompositionError(
                    "The resolved Agent roster requires the Harness UI subagent operator.",
                    code="subagent_operator_missing",
                )
            subagent_capability = SubagentCapability(async_enabled=True, operator=subagent_operator)
        node = composition.root
        host_tools = set()
        if subagent:
            host_tools.add("send_parent_message")
            node = node.model_copy(update={"instructions": (guidance("child"), *node.instructions)})
        elif dynamic:
            host_tools.add("spawn_subagent")
        if node.tools is not None:
            node = node.model_copy(update={"tools": (*node.tools, *host_tools)})
        if subagent_operator is not None and (subagent or has_children):
            root_capabilities = (
                *root_capabilities,
                SubagentTools(subagent_operator, composition, subagent=subagent, has_children=has_children),
            )
        if memory is not None:
            root_capabilities = (*root_capabilities, memory)
        plugin_keys = tuple(
            dict.fromkeys(recipe.plugin_key for node in composition.agent_nodes() for recipe in node.harness_plugins)
        )
        try:
            plugin_catalog = self._catalog.plugin_catalog(plugin_keys)
            environment_profile = composition.environment_profile
            environment_adapter = self._catalog.environment_adapter(
                environment_profile.adapter_key,
                environment_profile.provider_key,
            )
            path_layout = EnvironmentPathLayout.resolve(
                canonical_host_paths=environment_adapter.preserves_host_paths,
                project_roots=composition.project_roots,
                device_bindings=tuple(
                    (item.selection.alias, item.selection.working_directory)
                    for item in composition.environment_bindings
                ),
                user_skills_root=self._user_skills_root,
                content_plugins=tuple(
                    (item.plugin_id, item.path, item.skills_path) for item in composition.content_plugins
                ),
            )
            model_recipes = {model_recipe_id(recipe): recipe for recipe in composition.media_understanding.values()}
            definition = self._definition(
                node,
                plugin_catalog=plugin_catalog,
                subagent_capability=subagent_capability,
                root_capabilities=(*root_capabilities, ToolExecutionCapability(composition.tool_execution)),
                root=True,
                path_layout=path_layout,
                model_recipes=model_recipes,
                native_default_tools=(
                    environment_profile.profile_id == FULL_CONTROL_PROFILE.profile_id
                    and environment_profile.provider_key == FULL_CONTROL_PROFILE.provider_key
                    and environment_profile.adapter_key == FULL_CONTROL_PROFILE.adapter_key
                ),
            )
            model_resolver = HarnessUiModelResolver(
                model_recipes,
                subscription_sources=subscription_sources,
                api_keys=self._api_keys,
                configuration=composition.run_configuration,
            )
            pricing = model_resolver.pricing(pricing_catalog)
            definition = definition.with_updates(capabilities=(*definition.capabilities, pricing))
            compaction = (
                CompactionRun(background_compaction, model_resolver.fresh(), pricing)
                if background_compaction is not None and composition.background_compaction.enabled
                else None
            )
            if compaction is not None:
                definition = replace(
                    definition,
                    capabilities=tuple(
                        CompactionCapability(
                            item.policy,
                            background=composition.background_compaction,
                            operator=compaction,
                        )
                        if isinstance(item, CompactionCapability)
                        else item
                        for item in definition.capabilities
                    ),
                )
            executable = HarnessBuilder(
                configured_plugins_enabled=False,
                instrumentation=self._instrumentation,
            ).build(definition, pricing_catalog=pricing_catalog)
        except CompositionError:
            raise
        except (HarnessError, PluginError, ValueError, TypeError) as exc:
            raise CompositionError(
                "The resolved Run composition could not be constructed by Harness.",
                code="run_composition_reconstruction_failed",
            ) from exc
        return ReconstructedAgent(
            run_configuration=composition.run_configuration,
            memory_cursors=cursors,
            executable=cast(ExecutableAgent[str | None], executable),
            model_resolver=model_resolver,
            compaction=compaction,
            definition_capability_ids=frozenset(item.id for item in definition.capabilities if item.id is not None),
            media_models={
                kind: recipe.model_copy(deep=True) for kind, recipe in composition.media_understanding.items()
            },
        )

    def _reconstruct_memory(
        self,
        composition: ResolvedRunComposition,
        organization: MemoryOrganizationRun,
        root_capabilities: Sequence[AbstractCapability[Any]],
        subscription_sources: Mapping[str, SubscriptionSource] | None,
        pricing_catalog: PricingCatalog | None,
    ) -> ReconstructedAgent:
        node = composition.root
        recipe_id = model_recipe_id(node.model)
        memory = FileMemoryCapability(
            (
                FileMount(
                    name=organization.scope.name,
                    store=organization.store,
                    access="write",
                    always_load=("MEMORY.md",),
                ),
            )
        )
        resolver = HarnessUiModelResolver(
            {recipe_id: node.model},
            subscription_sources=subscription_sources,
            api_keys=self._api_keys,
            configuration=composition.run_configuration,
        )
        definition = AgentDefinition(
            agent=AgentSpec(
                model=recipe_id,
                system_prompt=list(node.system_prompt),
                instructions=list(node.instructions),
                model_settings=dict(node.model.settings),
                model_characteristics=node.model.model_characteristics,
                usage_limits=UsageLimits(request_limit=12),
            ),
            output_type=str,
            capabilities=(
                *root_capabilities,
                memory,
                CurrentAgentContextCapability(),
                resolver.pricing(pricing_catalog),
            ),
        )
        executable = HarnessBuilder(
            configured_plugins_enabled=False,
            instrumentation=self._instrumentation,
        ).build(definition, pricing_catalog=pricing_catalog)
        return ReconstructedAgent(
            executable=cast(ExecutableAgent[str | None], executable),
            model_resolver=resolver,
            run_configuration=composition.run_configuration,
            definition_capability_ids=frozenset(item.id for item in definition.capabilities if item.id is not None),
        )

    def _definition(
        self,
        node: ResolvedAgentNode,
        *,
        plugin_catalog: HarnessPluginFactoryCatalog,
        subagent_capability: SubagentCapability | None,
        root_capabilities: tuple[AbstractCapability[Any], ...],
        root: bool,
        path_layout: EnvironmentPathLayout,
        model_recipes: dict[str, ResolvedModelRecipe],
        native_default_tools: bool,
    ) -> AgentDefinition[str | None]:
        recipe_id = model_recipe_id(node.model)
        previous = model_recipes.setdefault(recipe_id, node.model)
        if previous != node.model:
            raise CompositionError("Model recipe identity collision.", code="model_recipe_collision")

        selections = [(item.capability, capability_configuration(item, model_recipes)) for item in node.capabilities]
        selected = self._catalog.capabilities(tuple(selections), path_layout=path_layout)
        capabilities: list[AbstractCapability[Any]] = [CurrentAgentContextCapability()]
        local_sources: dict[str, AbstractCapability[Any]] = {}
        for recipe, item in zip(node.capabilities, selected, strict=True):
            capability = item.capability
            if recipe.guardian_credits and isinstance(capability, ToolPermissionsCapability):
                capability = ToolPermissionsCapability(
                    capability.permissions,
                    review=capability.config,
                    review_capabilities=(GuardianReviewCapability(),),
                )
                capabilities.append(GuardianParentCapability())
            capabilities.append(capability)
            local_sources[recipe.capability] = capability
        if node.global_guidance is not None:
            capabilities.append(_GlobalGuidanceCapability(node.global_guidance))
        mcp_sources = {
            item.server_id: HarnessUiMCP(
                item,
                configuration_root=self._configuration_root,
                oauth=self._mcp_oauth,
                apps=self._mcp_apps if item.apps_enabled else None,
                connections=self._mcp_connections,
            )
            for item in node.mcp_servers
        }
        capabilities.extend(mcp_sources.values())
        tool_search = (
            ToolSearchPlan(
                groups={
                    name: ToolSearchSelection(
                        description=group.description,
                        capabilities=tuple(mcp_sources[source] for source in group.mcp_servers)
                        + tuple(local_sources[source] for source in group.capabilities),
                        plugins=group.harness_plugins,
                    )
                    for name, group in node.tool_search.groups.items()
                },
                config=node.tool_search.config,
            )
            if node.tool_search is not None and node.tool_search.enabled
            else None
        )
        if subagent_capability is not None and (root or node.children):
            capabilities.append(subagent_capability)
        if node.tools is not None:
            # Controls are derived presentation, not authority for every member.
            # Filter the actual canonical target directory before generating them.
            controls = (
                frozenset(
                    {
                        tool_search.config.search_name,
                        tool_search.config.describe_name,
                        tool_search.config.call_name,
                        tool_search.config.list_name,
                        tool_search.config.groups_name,
                    }
                )
                if tool_search is not None
                else frozenset()
            )
            if root:
                controls |= frozenset(
                    {
                        "discover_connector_tools",
                        "describe_connector_tool",
                        "call_connector_tool",
                        "list_connector_tools",
                        "list_connectors",
                    }
                )
            if node.source_kind == "bot":
                # Captured targets add Computer tools only after being opened.
                # Valid deferred selectors remain allowlisted, not mandatory at step one.
                controls |= ComputerToolset.available_names()
                environment = next(
                    item.configuration for item in node.capabilities if item.capability == "dynamic_environment"
                )
                # Bot overrides may intentionally hide tools selected by its Agent.
                # Keep their exact selectors without requiring disabled tools to exist.
                if environment.get("files_enabled") is False:
                    controls |= FileToolset.available_names((FILE_EXECUTION_ACTIONS,))
                if environment.get("shell_enabled") is False:
                    controls |= ShellToolset.available_names(FILE_EXECUTION_ACTIONS)
            capabilities.append(
                _ToolAllowlistCapability(
                    names=frozenset(node.tools),
                    optional_controls=controls,
                    mcp_groups={
                        source.id: name
                        for name, group in tool_search.groups.items()
                        for source in group.capabilities
                        if isinstance(source, HarnessUiMCP) and source.id is not None
                    }
                    if tool_search is not None
                    else None,
                )
            )
        elif native_default_tools:
            capabilities.append(_NativeDefaultToolsCapability())
        if root:
            capabilities.extend(root_capabilities)

        plugins = tuple(
            plugin_catalog.create_plugin(
                HarnessPluginFactoryContext(
                    plugin_key=item.plugin_key,
                    plugin_id=item.plugin_id,
                    configuration=item.configuration,
                    extensions={},
                )
            )
            for item in node.harness_plugins
        )
        children = tuple(
            SubagentDefinition(
                name=item.name,
                description=(
                    item.description if item.instruction is None else f"{item.description}\n\n{item.instruction}"
                ),
                agent=self._definition(
                    item.definition,
                    plugin_catalog=plugin_catalog,
                    subagent_capability=subagent_capability,
                    root_capabilities=(),
                    root=False,
                    path_layout=path_layout,
                    model_recipes=model_recipes,
                    native_default_tools=native_default_tools,
                ),
            )
            for item in node.children
        )
        return AgentDefinition(
            agent=AgentSpec(
                model=recipe_id,
                usage_limits=UsageLimits(request_limit=None),
                model_settings=dict(node.model.settings),
                model_characteristics=node.model.model_characteristics,
                system_prompt=list(node.system_prompt),
                instructions=list(node.instructions),
            ),
            output_type=[str, ToolOutput(_finish_bot_silently, name="finish_silently")]
            if node.source_kind == "bot"
            else str,
            definition_id=f"a13n-harness-ui:{node.source_kind}:{node.source_id}",
            capabilities=tuple(capabilities),
            plugins=plugins,
            tool_search=tool_search,
            subagents=children,
            model_recovery=ModelRecoveryPolicy(
                enabled=True,
                continuation_prompt=(
                    TextContent(
                        DEFAULT_RECOVERY_PROMPT,
                        metadata={"display": False, "source_id": "a13n-harness-ui.model-recovery"},
                    ),
                ),
            ),
        )


__all__ = ["AgentReconstructor", "ReconstructedAgent"]
