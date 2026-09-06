"""Fresh native Harness construction from one resolved Agent UI Run composition."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from a13n_harness import AgentContext, AgentDefinition, AgentSpec, ExecutableAgent, HarnessBuilder, SubagentDefinition
from a13n_harness.capabilities import SubagentCapability, SubagentOperator
from a13n_harness.errors import HarnessError, PluginError
from a13n_harness.plugin_factories import HarnessPluginFactoryCatalog, HarnessPluginFactoryContext
from a13n_harness.pricing import PricingCatalog
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.toolsets import AbstractToolset, ToolsetTool, WrapperToolset

from a13n_ui.environment_paths import EnvironmentPathLayout
from a13n_ui.errors import CompositionError
from a13n_ui.extensions import AgentUiExtensionCatalog
from a13n_ui.mcp_adapters import AgentUiMCP
from a13n_ui.model_runtime import AgentUiModelResolver, SubscriptionSource, model_recipe_id

from .models import ResolvedAgentNode, ResolvedModelRecipe, ResolvedRunComposition


@dataclass(frozen=True, slots=True)
class ReconstructedAgent:
    """One fresh executable and the Run bindings needed to invoke it."""

    executable: ExecutableAgent[str]
    model_resolver: AgentUiModelResolver
    definition_capability_ids: frozenset[str]


class _ToolAllowlistCapability(AbstractCapability[AgentContext]):
    id: str | None = "a13n.ui.tool-allowlist"

    def __init__(self, names: frozenset[str]) -> None:
        self.names = names

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(position="innermost")

    def get_wrapper_toolset(self, toolset: AbstractToolset[AgentContext]) -> AbstractToolset[AgentContext]:
        return _ToolAllowlistToolset(toolset, self.names)


class _ToolAllowlistToolset(WrapperToolset[AgentContext]):
    def __init__(self, wrapped: AbstractToolset[AgentContext], names: frozenset[str]) -> None:
        super().__init__(wrapped)
        self._names = names

    async def get_tools(self, ctx: RunContext[AgentContext]) -> dict[str, ToolsetTool[AgentContext]]:
        tools = await self.wrapped.get_tools(ctx)
        missing = self._names - tools.keys()
        if missing:
            raise CompositionError(
                "The selected tool allowlist contains an unavailable tool.",
                code="tool_selection_missing",
                details={"tool": sorted(missing)[0]},
            )
        return {name: tool for name, tool in tools.items() if name in self._names}


class AgentReconstructor:
    """Build a fresh native Agent graph without resolving credentials or entering Environments."""

    def __init__(
        self,
        catalog: AgentUiExtensionCatalog | None = None,
        *,
        user_skills_root: Path | None = None,
    ) -> None:
        self._catalog = catalog or AgentUiExtensionCatalog()
        self._user_skills_root = user_skills_root

    def reconstruct(
        self,
        composition: ResolvedRunComposition,
        *,
        subagent_operator: SubagentOperator | None,
        root_capabilities: Sequence[AbstractCapability[Any]] = (),
        subscription_sources: Mapping[str, SubscriptionSource] | None = None,
        pricing_catalog: PricingCatalog | None = None,
    ) -> ReconstructedAgent:
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
                user_skills_root=self._user_skills_root,
                content_plugins=tuple(
                    (item.plugin_id, item.path, item.skills_path) for item in composition.content_plugins
                ),
            )
            model_recipes: dict[str, ResolvedModelRecipe] = {}
            definition = self._definition(
                composition.root,
                plugin_catalog=plugin_catalog,
                subagent_operator=subagent_operator,
                root_capabilities=tuple(root_capabilities),
                root=True,
                path_layout=path_layout,
                model_recipes=model_recipes,
            )
            executable = HarnessBuilder(configured_plugins_enabled=False).build(
                definition, pricing_catalog=pricing_catalog
            )
        except CompositionError:
            raise
        except (HarnessError, PluginError, ValueError, TypeError) as exc:
            raise CompositionError(
                "The resolved Run composition could not be constructed by Harness.",
                code="run_composition_reconstruction_failed",
            ) from exc
        return ReconstructedAgent(
            executable=cast(ExecutableAgent[str], executable),
            model_resolver=AgentUiModelResolver(
                model_recipes,
                subscription_sources=subscription_sources,
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
    ) -> AgentDefinition[str]:
        recipe_id = model_recipe_id(node.model)
        previous = model_recipes.setdefault(recipe_id, node.model)
        if previous != node.model:
            raise CompositionError("Model recipe identity collision.", code="model_recipe_collision")

        selections = []
        for item in node.capabilities:
            configuration = dict(item.configuration)
            if item.model is not None:
                auxiliary_id = model_recipe_id(item.model)
                previous = model_recipes.setdefault(auxiliary_id, item.model)
                if previous != item.model:
                    raise CompositionError("Model recipe identity collision.", code="model_recipe_collision")
                configuration["model"] = auxiliary_id
                overrides = configuration.get("model_settings", {})
                if not isinstance(overrides, dict):
                    raise CompositionError(
                        "Auxiliary Model settings must be an object.", code="capability_model_settings_invalid"
                    )
                configuration["model_settings"] = {**item.model.settings, **overrides}
            selections.append((item.capability, configuration))
        selected = self._catalog.capabilities(tuple(selections), path_layout=path_layout)
        capabilities: list[AbstractCapability[Any]] = [item.capability for item in selected]
        capabilities.extend(AgentUiMCP(item) for item in node.mcp_servers)
        if node.children:
            if not isinstance(subagent_operator, SubagentOperator):
                raise CompositionError(
                    "The resolved Agent roster requires the Agent UI subagent operator.",
                    code="subagent_operator_missing",
                )
            capabilities.append(SubagentCapability(async_enabled=True, operator=subagent_operator))
        if node.tools is not None:
            capabilities.append(_ToolAllowlistCapability(names=frozenset(node.tools)))
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
                ),
            )
            for item in node.children
        )
        return AgentDefinition(
            agent=AgentSpec(
                model=recipe_id,
                model_settings=dict(node.model.settings),
                system_prompt=list(node.instructions if node.system_prompt is None else node.system_prompt),
                instructions=list(node.instructions) if node.system_prompt is not None else None,
            ),
            output_type=str,
            definition_id=f"agent-ui:{node.source_kind}:{node.source_id}",
            capabilities=tuple(capabilities),
            plugins=plugins,
            subagents=children,
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
