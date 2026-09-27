"""Fresh native Harness construction from one resolved Harness UI Run composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentSpec,
    ExecutableAgent,
    HarnessBuilder,
    HarnessInstrumentation,
    ModelRecoveryPolicy,
    SubagentDefinition,
)
from a13n_harness.capabilities import SubagentCapability, SubagentOperator, ToolProxyPlan, ToolProxySelection
from a13n_harness.capabilities.memory import FileMemoryCapability, FileMount, MemoryCursors
from a13n_harness.errors import HarnessError, PluginError
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
from a13n_harness.tools import HARNESS_TOOL_METADATA_KEY
from a13n_harness.tools.metadata import normalize_harness_tool_metadata
from a13n_harness.toolsets.file_media import NativeInputMediaKind
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import TextContent
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset
from pydantic_ai.usage import UsageLimits

from a13n_harness_ui.environment_paths import EnvironmentPathLayout
from a13n_harness_ui.environment_profiles import FULL_CONTROL_PROFILE
from a13n_harness_ui.errors import CompositionError
from a13n_harness_ui.extensions import HarnessUiExtensionCatalog
from a13n_harness_ui.mcp_adapters import HarnessUiMCP
from a13n_harness_ui.mcp_apps.connections import Connections
from a13n_harness_ui.media_understanding import FileMediaUnderstanding
from a13n_harness_ui.memory import MemoryOrganizationRun, bind_memory
from a13n_harness_ui.model_accounts.api_keys import ApiKeyStore
from a13n_harness_ui.model_runtime import HarnessUiModelResolver, SubscriptionSource, model_recipe_id

from .auxiliary import capability_configuration
from .models import ResolvedAgentNode, ResolvedModelRecipe, ResolvedRunComposition


@dataclass(frozen=True, slots=True)
class ReconstructedAgent:
    """One fresh executable and the Run bindings needed to invoke it."""

    executable: ExecutableAgent[str]
    model_resolver: HarnessUiModelResolver
    definition_capability_ids: frozenset[str]
    media_models: Mapping[NativeInputMediaKind, ResolvedModelRecipe] = field(default_factory=dict)
    memory_cursors: MemoryCursors = field(default_factory=MemoryCursors)

    def file_media_understanding(self, thread_id: str) -> FileMediaUnderstanding | None:
        if not self.media_models:
            return None
        return FileMediaUnderstanding(self.media_models, self.model_resolver.fresh(), thread_id=thread_id)


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
        return ModelContextProjection(
            blocks=(
                ModelContextBlock(
                    source_id="a13n-harness-ui.global-guidance",
                    placement=ModelContextPlacement.REQUEST_EPILOGUE,
                    content=content,
                ),
                *projection.blocks,
            )
        )


class _ToolAllowlistCapability(AbstractCapability[AgentContext]):
    id: str | None = "a13n.ui.tool-allowlist"

    def __init__(self, names: frozenset[str], *, optional_controls: frozenset[str] = frozenset()) -> None:
        self.names = names
        self.optional_controls = optional_controls

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return _ToolAllowlistToolset(toolset, self.names, self.optional_controls)


@dataclass
class _ToolAllowlistToolset(WrapperToolset[AgentContext]):
    names: frozenset[str]
    optional_controls: frozenset[str] = frozenset()

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        missing = self.names - tools.keys() - self.optional_controls
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
    ) -> None:
        self._instrumentation: HarnessInstrumentation | Literal["environment"] | None = instrumentation
        self._mcp_apps = mcp_apps
        self._api_keys = api_keys
        self._configuration_root = configuration_root
        self._catalog = catalog or HarnessUiExtensionCatalog()
        self._user_skills_root = user_skills_root

    def reconstruct(
        self,
        composition: ResolvedRunComposition,
        *,
        subagent_operator: SubagentOperator | None,
        root_capabilities: Sequence[AbstractCapability[Any]] = (),
        subscription_sources: Mapping[str, SubscriptionSource] | None = None,
        pricing_catalog: PricingCatalog | None = None,
        memory_positions: Mapping[str, str | None] | None = None,
        organization: MemoryOrganizationRun | None = None,
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
        )
        if memory is not None:
            root_capabilities = (*root_capabilities, memory)
        plugin_keys = tuple(
            dict.fromkeys(
                recipe.plugin_key for node in _walk_nodes(composition.root) for recipe in node.harness_plugins
            )
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
                composition.root,
                plugin_catalog=plugin_catalog,
                subagent_operator=subagent_operator,
                root_capabilities=tuple(root_capabilities),
                root=True,
                path_layout=path_layout,
                model_recipes=model_recipes,
                native_default_tools=(
                    environment_profile.profile_id == FULL_CONTROL_PROFILE.profile_id
                    and environment_profile.provider_key == FULL_CONTROL_PROFILE.provider_key
                    and environment_profile.adapter_key == FULL_CONTROL_PROFILE.adapter_key
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
            memory_cursors=cursors,
            executable=cast(ExecutableAgent[str], executable),
            model_resolver=HarnessUiModelResolver(
                model_recipes,
                subscription_sources=subscription_sources,
                api_keys=self._api_keys,
            ),
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
            capabilities=(*root_capabilities, memory),
        )
        executable = HarnessBuilder(
            configured_plugins_enabled=False,
            instrumentation=self._instrumentation,
        ).build(definition, pricing_catalog=pricing_catalog)
        return ReconstructedAgent(
            executable=cast(ExecutableAgent[str], executable),
            model_resolver=HarnessUiModelResolver(
                {recipe_id: node.model},
                subscription_sources=subscription_sources,
                api_keys=self._api_keys,
            ),
            definition_capability_ids=frozenset(item.id for item in definition.capabilities if item.id is not None),
        )

    def _definition(
        self,
        node: ResolvedAgentNode,
        *,
        plugin_catalog: HarnessPluginFactoryCatalog,
        subagent_operator: SubagentOperator | None,
        root_capabilities: tuple[AbstractCapability[Any], ...],
        root: bool,
        path_layout: EnvironmentPathLayout,
        model_recipes: dict[str, ResolvedModelRecipe],
        native_default_tools: bool,
    ) -> AgentDefinition[str]:
        recipe_id = model_recipe_id(node.model)
        previous = model_recipes.setdefault(recipe_id, node.model)
        if previous != node.model:
            raise CompositionError("Model recipe identity collision.", code="model_recipe_collision")

        selections = [(item.capability, capability_configuration(item, model_recipes)) for item in node.capabilities]
        selected = self._catalog.capabilities(tuple(selections), path_layout=path_layout)
        capabilities: list[AbstractCapability[Any]] = [item.capability for item in selected]
        if node.global_guidance is not None:
            capabilities.append(_GlobalGuidanceCapability(node.global_guidance))
        mcp_sources = {
            item.server_id: HarnessUiMCP(
                item,
                configuration_root=self._configuration_root,
                apps=self._mcp_apps if item.apps_enabled else None,
            )
            for item in node.mcp_servers
        }
        capabilities.extend(mcp_sources.values())
        tool_proxy = (
            ToolProxyPlan(
                groups={
                    name: ToolProxySelection(
                        description=group.description,
                        capabilities=tuple(mcp_sources[source] for source in group.mcp_servers),
                        plugins=group.harness_plugins,
                    )
                    for name, group in node.tool_proxy.groups.items()
                },
                config=node.tool_proxy.config,
            )
            if node.tool_proxy is not None
            else None
        )
        if node.children:
            if not isinstance(subagent_operator, SubagentOperator):
                raise CompositionError(
                    "The resolved Agent roster requires the Harness UI subagent operator.",
                    code="subagent_operator_missing",
                )
            capabilities.append(SubagentCapability(async_enabled=True, operator=subagent_operator))
        if node.tools is not None:
            # Controls are derived presentation, not authority for every member.
            # Filter the actual canonical target directory before generating them.
            controls = (
                frozenset({tool_proxy.config.search_name, tool_proxy.config.call_name})
                if tool_proxy is not None
                else frozenset()
            )
            capabilities.append(_ToolAllowlistCapability(names=frozenset(node.tools), optional_controls=controls))
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
                    subagent_operator=subagent_operator,
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
            output_type=str,
            definition_id=f"a13n-harness-ui:{node.source_kind}:{node.source_id}",
            capabilities=tuple(capabilities),
            plugins=plugins,
            tool_proxy=tool_proxy,
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


def _walk_nodes(root: ResolvedAgentNode) -> tuple[ResolvedAgentNode, ...]:
    values: list[ResolvedAgentNode] = []
    pending = [root]
    while pending:
        node = pending.pop()
        values.append(node)
        pending.extend(reversed(tuple(item.definition for item in node.children)))
    return tuple(values)


__all__ = ["AgentReconstructor", "ReconstructedAgent"]
