"""Trusted process-local reconstruction of pinned Agent snapshots."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from a13n_harness import (
    AgentDefinition,
    AgentSpec,
    ExecutableAgent,
    HarnessBuilder,
    SubagentDefinition,
)
from a13n_harness import (
    __version__ as harness_version,
)
from a13n_harness.capabilities import SubagentCapability, SubagentOperator
from a13n_harness.environment import DynamicEnvironmentCapability, DynamicEnvironmentConfiguration
from a13n_harness.errors import HarnessError
from a13n_harness.plugin_factories import (
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryContext,
)
from pydantic_ai.capabilities import AbstractCapability

from a13n_ui.errors import CompositionError
from a13n_ui.mcp_adapters import AgentUiMCP
from a13n_ui.model_runtime import AgentUiModelResolver, model_recipe_id

from .catalogs import (
    mcp_adapter_lock,
    plugin_dependency_lock,
    pydantic_ai_adapter_lock,
    selected_plugin_catalog,
)
from .models import ResolvedAgentNode, ResolvedAgentSnapshot

type PortableCapabilityFactory = Callable[[], AbstractCapability[Any]]


@dataclass(frozen=True, slots=True)
class ReconstructedAgent:
    """One executable, its Model resolver, and required Host run attachments."""

    executable: ExecutableAgent[str]
    model_resolver: AgentUiModelResolver
    run_capability_ids: frozenset[str] = frozenset()


class AgentReconstructor:
    """Verify installed provenance and build the exact pinned Harness graph."""

    def __init__(
        self,
        *,
        plugin_catalog: HarnessPluginFactoryCatalog | None = None,
        portable_capabilities: Mapping[str, PortableCapabilityFactory] | None = None,
    ) -> None:
        self._injected_plugin_catalog = plugin_catalog
        self._portable_capabilities = MappingProxyType(dict(portable_capabilities or {}))

    def reconstruct(
        self,
        snapshot: ResolvedAgentSnapshot,
        *,
        subagent_operator: SubagentOperator | None,
        root_capabilities: Sequence[AbstractCapability[Any]] = (),
    ) -> ReconstructedAgent:
        """Reconstruct without resolving credentials, entering Environments, or starting work."""

        plugin_catalog = self._verify_snapshot(snapshot)
        recipes: dict[str, object] = {}
        definition = self._definition(
            snapshot.root,
            plugin_catalog=plugin_catalog,
            subagent_operator=subagent_operator,
            root_capabilities=tuple(root_capabilities),
            is_root=True,
            model_recipes=recipes,
        )
        typed_recipes = {key: value for key, value in recipes.items() if isinstance(value, type(snapshot.root.model))}
        if len(typed_recipes) != len(recipes):  # pragma: no cover - private construction is closed
            raise TypeError("invalid Model recipe collection")
        try:
            executable = HarnessBuilder(configured_plugins_enabled=False).build(definition)
        except HarnessError as exc:
            raise CompositionError(
                "The pinned Agent snapshot could not be built by Harness.",
                code="agent_reconstruction_failed",
            ) from exc
        return ReconstructedAgent(
            executable=cast(ExecutableAgent[str], executable),
            model_resolver=AgentUiModelResolver(typed_recipes),
            run_capability_ids=frozenset(
                capability.id for capability in definition.capabilities if capability.id is not None
            ),
        )

    def _verify_snapshot(self, snapshot: ResolvedAgentSnapshot) -> HarnessPluginFactoryCatalog:
        if snapshot.harness_release != harness_version:
            raise CompositionError(
                "The pinned Agent snapshot requires a different Harness release.",
                code="agent_snapshot_incompatible",
            )
        expected_model = pydantic_ai_adapter_lock()
        expected_mcp = mcp_adapter_lock()
        for lock in snapshot.dependencies:
            if lock.dependency_kind == "model-adapter" and lock != expected_model:
                raise _provenance_mismatch(lock.key)
            if lock.dependency_kind == "mcp-adapter" and lock != expected_mcp:
                raise _provenance_mismatch(lock.key)

        plugin_keys = tuple(sorted({lock.key for lock in snapshot.dependencies if lock.dependency_kind == "plugin"}))
        catalog = selected_plugin_catalog(plugin_keys, catalog=self._injected_plugin_catalog)
        registrations = {item.plugin_key: item for item in catalog.registrations}
        for lock in snapshot.dependencies:
            if lock.dependency_kind != "plugin":
                continue
            registration = registrations.get(lock.key)
            if registration is None or plugin_dependency_lock(registration) != lock:
                raise _provenance_mismatch(lock.key)
        return catalog

    def _definition(
        self,
        node: ResolvedAgentNode,
        *,
        plugin_catalog: HarnessPluginFactoryCatalog,
        subagent_operator: SubagentOperator | None,
        root_capabilities: tuple[AbstractCapability[Any], ...],
        is_root: bool,
        model_recipes: dict[str, object],
    ) -> AgentDefinition[str]:
        model_id = model_recipe_id(node.model)
        previous = model_recipes.setdefault(model_id, node.model)
        if previous != node.model:
            raise CompositionError(
                "Two pinned Model recipes map to the same logical ID.",
                code="model_recipe_collision",
            )

        capabilities: list[AbstractCapability[Any]] = [
            *self._portable_node_capabilities(node),
            *(AgentUiMCP(recipe) for recipe in node.mcp_servers),
        ]
        if node.children:
            if not isinstance(subagent_operator, SubagentOperator):
                raise CompositionError(
                    "The pinned Agent roster requires the Agent UI async subagent operator.",
                    code="subagent_operator_missing",
                )
            capabilities.append(SubagentCapability(async_enabled=True, operator=subagent_operator))
        if is_root:
            capabilities.extend(root_capabilities)

        children = tuple(
            SubagentDefinition(
                name=edge.name,
                description=_edge_description(edge.description, edge.instruction),
                agent=self._definition(
                    edge.definition,
                    plugin_catalog=plugin_catalog,
                    subagent_operator=subagent_operator,
                    root_capabilities=(),
                    is_root=False,
                    model_recipes=model_recipes,
                ),
            )
            for edge in node.children
        )
        plugins = tuple(
            plugin_catalog.create_plugin(
                HarnessPluginFactoryContext(
                    plugin_key=recipe.plugin_key,
                    plugin_id=recipe.instance_name,
                    configuration=recipe.configuration,
                    extensions={},
                )
            )
            for recipe in node.plugins
        )
        spec = AgentSpec(
            model=model_id,
            model_settings=dict(node.model.settings),
            system_prompt=list(node.instructions),
        )
        return AgentDefinition(
            agent=spec,
            output_type=str,
            definition_id=f"agent-ui:{node.definition_digest}",
            capabilities=tuple(capabilities),
            plugins=plugins,
            subagents=children,
        )

    def _portable_node_capabilities(self, node: ResolvedAgentNode) -> tuple[AbstractCapability[Any], ...]:
        available = self._portable_capabilities
        if node.tools is None:
            selected = list(available)
        else:
            missing = [name for name in node.tools if name not in available]
            if missing:
                raise CompositionError(
                    "A Markdown child selects a portable tool family unavailable in this Agent UI release.",
                    code="portable_tool_unavailable",
                    details={"tool": missing[0]},
                )
            selected = list(node.tools)
        if node.optional_tools is not None:
            selected.extend(name for name in node.optional_tools if name in available and name not in selected)
        capabilities: list[AbstractCapability[Any]] = []
        environment_files = False
        environment_shell = False
        for name in selected:
            capability = available[name]()
            if not isinstance(capability, AbstractCapability):
                raise CompositionError(
                    "An Agent UI portable tool factory returned an invalid Capability.",
                    code="portable_tool_invalid",
                    details={"tool": name},
                )
            if isinstance(capability, DynamicEnvironmentCapability):
                environment_files = environment_files or capability.configuration.files_enabled
                environment_shell = environment_shell or capability.configuration.shell_enabled
            else:
                capabilities.append(capability)
        if environment_files or environment_shell:
            capabilities.append(
                DynamicEnvironmentCapability(
                    DynamicEnvironmentConfiguration(
                        files_enabled=environment_files,
                        shell_enabled=environment_shell,
                    )
                )
            )
        return tuple(capabilities)


def _edge_description(description: str, instruction: str | None) -> str:
    if instruction is None:
        return description
    return f"{description}\n\n{instruction}"


def _provenance_mismatch(key: str) -> CompositionError:
    return CompositionError(
        "Installed trusted runtime provenance does not match the pinned Agent snapshot.",
        code="agent_snapshot_provenance_mismatch",
        details={"key": key},
    )


__all__ = [
    "AgentReconstructor",
    "PortableCapabilityFactory",
    "ReconstructedAgent",
]
