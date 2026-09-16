"""Trusted reconstruction of frozen Agent configuration into Harness values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from functools import partial
from typing import Any, Protocol

from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentSpec,
    ModelRecoveryPolicy,
    RunBindings,
    SubagentDefinition,
)
from a13n_harness import (
    DelegationContextPolicy as HarnessDelegationContextPolicy,
)
from a13n_harness.capabilities import SubagentCapability, ToolReviewConfig
from a13n_harness.environment import DynamicEnvironmentCapability
from a13n_harness.errors import HarnessError
from a13n_harness.output_schema import structured_output_type
from a13n_harness.plugin_factories import (
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryContext,
)
from a13n_harness.plugins import AbstractHarnessPlugin
from a13n_harness.tools import (
    ToolPermissions,
    ToolPermissionsCapability,
    ToolPermissionSetting,
    source_tool_id,
    source_tool_prefix,
)
from a13n_harness.tools.client import (
    ClientToolsCapability,
    ClientToolsetDefinition,
    ClientToolsSpec,
)
from pydantic_ai.agent.abstract import AgentRetries
from pydantic_ai.capabilities import AbstractCapability
from referencing import Registry, Resource
from referencing.exceptions import CannotDetermineSpecification, Unresolvable
from referencing.jsonschema import DRAFT202012

from a13n_service.connectivity.toolsets import connection_source_key
from a13n_service.digests import digest_request
from a13n_service.web.runtime import web_capability

from .domain import (
    EffectiveAgentConfig,
    OutputSpec,
    PluginSelection,
    PreparedAgentPlugins,
)
from .plugin_preparation import PluginSelectionError, validate_plugin_selections
from .resolution import MAX_SUBAGENT_DEPTH, MAX_SUBAGENT_NODES
from .toolsets import builtin_permission_rules, environment_configuration, web_selection

_CLIENT_TOOLSET_ID = "service"
_MAX_SCHEMA_REFERENCE_EXPANSIONS = 1024


class AgentDefinitionReconstructionError(Exception):
    """A bounded failure while rebuilding one already accepted Agent graph."""

    def __init__(self, reason: str, *, path: str | None = None) -> None:
        super().__init__(reason)
        self.code = "agent_reconstruction_failed"
        self.reason = reason
        self.path = path


@dataclass(frozen=True, slots=True)
class AgentDefinitionReconstructionContext:
    """Frozen node facts available to trusted definition-Capability composition."""

    agent_id: str
    agent_revision_id: str | None
    content_digest: str
    is_root: bool
    config: EffectiveAgentConfig


class AgentDefinitionCapabilityProvider(Protocol):
    """Construct fresh trusted definition Capabilities for one frozen graph node."""

    def __call__(
        self,
        context: AgentDefinitionReconstructionContext,
        /,
    ) -> Sequence[AbstractCapability[AgentContext]]: ...


class AgentRunBindingsProvider(Protocol):
    """Supply typed Run collaborators for one frozen graph node."""

    def __call__(
        self,
        context: AgentDefinitionReconstructionContext,
        bindings: RunBindings,
        /,
    ) -> RunBindings: ...


class AgentReconstructor:
    """Rebuild exact process-local Harness definitions without mutable lookups or I/O."""

    def __init__(
        self,
        plugin_catalog: HarnessPluginFactoryCatalog,
        *,
        capability_provider: AgentDefinitionCapabilityProvider | None = None,
        run_bindings_provider: AgentRunBindingsProvider | None = None,
    ) -> None:
        if not isinstance(plugin_catalog, HarnessPluginFactoryCatalog):
            raise TypeError("plugin_catalog must be a HarnessPluginFactoryCatalog")
        self._plugin_catalog = plugin_catalog
        self._capability_provider = capability_provider
        self._run_bindings_provider = run_bindings_provider

    def reconstruct(
        self,
        *,
        agent_id: str,
        agent_revision_id: str | None,
        effective_config: EffectiveAgentConfig,
        subagent_capability: SubagentCapability,
        prepared_plugins: PreparedAgentPlugins,
    ) -> AgentDefinition[Any]:
        """Reconstruct the accepted root snapshot and its exact immutable child graph."""

        self.validate(
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            effective_config=effective_config,
            subagent_capability=subagent_capability,
            prepared_plugins=prepared_plugins,
        )
        root = AgentDefinitionReconstructionContext(
            agent_id=agent_id,
            agent_revision_id=agent_revision_id,
            content_digest=effective_config.content_digest,
            is_root=True,
            config=effective_config,
        )
        return self._definition(root, subagent_capability=subagent_capability, prepared_plugins=prepared_plugins)

    def validate(
        self,
        *,
        agent_id: str,
        agent_revision_id: str | None,
        effective_config: EffectiveAgentConfig,
        subagent_capability: SubagentCapability,
        prepared_plugins: PreparedAgentPlugins,
    ) -> None:
        """Validate the frozen graph and factories without constructing live plugins."""

        try:
            prepared_plugins.validate_for(effective_config)
        except ValueError as error:
            raise AgentDefinitionReconstructionError("plugin_preparation_mismatch") from error
        if effective_config.resolved_subagents and subagent_capability.async_enabled != (
            effective_config.subagent_mode == "async"
        ):
            raise AgentDefinitionReconstructionError("subagent_mode_mismatch")
        pending: list[tuple[str | None, EffectiveAgentConfig, PreparedAgentPlugins, tuple[str | None, ...]]] = [
            (agent_revision_id, effective_config, prepared_plugins, ())
        ]
        count = 0
        while pending:
            revision_id, config, plugins, ancestors = pending.pop()
            count += 1
            if len(ancestors) > MAX_SUBAGENT_DEPTH:
                raise AgentDefinitionReconstructionError("subagent_graph_too_deep")
            if revision_id in ancestors:
                raise AgentDefinitionReconstructionError("subagent_cycle")
            if count > MAX_SUBAGENT_NODES:
                raise AgentDefinitionReconstructionError("subagent_graph_too_large")
            self._verify_effective_config(config)
            if set(config.child_configs) != {edge.child_agent_revision_id for edge in config.resolved_subagents}:
                raise AgentDefinitionReconstructionError("subagent_snapshot_mismatch")
            try:
                validate_plugin_selections(self._plugin_catalog, plugins.plugins, retained=True)
            except PluginSelectionError as error:
                raise AgentDefinitionReconstructionError(error.reason, path=error.path) from error
            _output_type(config.output_spec)
            for edge in config.resolved_subagents:
                child = config.child_configs[edge.child_agent_revision_id]
                if child.agent_id != edge.child_agent_id:
                    raise AgentDefinitionReconstructionError("subagent_agent_mismatch", path=f"subagents.{edge.name}")
                pending.append(
                    (
                        edge.child_agent_revision_id,
                        child.effective_config,
                        plugins.children[edge.child_agent_revision_id],
                        (*ancestors, revision_id),
                    )
                )

    def _definition(
        self,
        node: AgentDefinitionReconstructionContext,
        *,
        subagent_capability: SubagentCapability,
        prepared_plugins: PreparedAgentPlugins,
    ) -> AgentDefinition[Any]:
        config = node.config
        child_definitions: list[SubagentDefinition] = []
        for edge in config.resolved_subagents:
            child = config.child_configs[edge.child_agent_revision_id]
            child_definition = self._definition(
                AgentDefinitionReconstructionContext(
                    agent_id=edge.child_agent_id,
                    agent_revision_id=edge.child_agent_revision_id,
                    content_digest=child.revision_content_digest,
                    is_root=False,
                    config=child.effective_config,
                ),
                subagent_capability=SubagentCapability(),
                prepared_plugins=prepared_plugins.children[edge.child_agent_revision_id],
            )
            child_definitions.append(
                SubagentDefinition(
                    name=edge.name,
                    description=(
                        edge.description
                        or child.effective_config.protocol.public_description
                        or child.effective_config.protocol.public_name
                    ),
                    agent=child_definition,
                    context=HarnessDelegationContextPolicy(
                        include_task=edge.context.include_task,
                        history=edge.context.history,
                        task_state=edge.context.task_state,
                    ),
                    usage_limits=edge.usage_limits,
                    run_bindings_factory=(
                        partial(
                            self._run_bindings_provider,
                            AgentDefinitionReconstructionContext(
                                agent_id=edge.child_agent_id,
                                agent_revision_id=edge.child_agent_revision_id,
                                content_digest=child.revision_content_digest,
                                is_root=False,
                                config=child.effective_config,
                            ),
                        )
                        if self._run_bindings_provider is not None
                        else None
                    ),
                )
            )

        capabilities = [
            DynamicEnvironmentCapability(environment_configuration(config.toolsets)),
            *self._provided_capabilities(node),
        ]
        review = None
        if config.reviewer is not None:
            if config.resolved_reviewer_model is None:
                raise AgentDefinitionReconstructionError("reviewer_model_missing", path="reviewer")
            review = ToolReviewConfig.model_validate(
                {
                    **config.reviewer.model_dump(),
                    "model": config.resolved_reviewer_model.execution.model_id,
                    "model_settings": dict(config.resolved_reviewer_model.settings),
                }
            )
        capabilities.append(ToolPermissionsCapability(_permissions(config), review=review))
        selected_web = web_selection(config.toolsets)
        if selected_web is not None:
            capabilities.append(web_capability(selected_web))
        if config.client_tools:
            capabilities.append(
                ClientToolsCapability(
                    spec=ClientToolsSpec(
                        default_toolsets=(
                            ClientToolsetDefinition(
                                toolset_id=_CLIENT_TOOLSET_ID,
                                tools=config.client_tools,
                            ),
                        ),
                        allow_run_override=False,
                    )
                )
            )
        if child_definitions:
            capabilities.append(subagent_capability)

        try:
            plugins = tuple(self._create_plugin(selection) for selection in prepared_plugins.plugins)
            output_type = _output_type(config.output_spec)
            retries = (
                None
                if config.retries is None
                else AgentRetries(tools=config.retries.tools, output=config.retries.output)
            )
            return AgentDefinition(
                agent=AgentSpec(
                    model=config.resolved_model.execution.model_id,
                    name=config.protocol.public_name,
                    description=config.protocol.public_description,
                    model_settings=dict(config.resolved_model.settings),
                    system_prompt=config.instructions,
                    retries=retries,
                    model_characteristics=config.resolved_model.characteristics,
                ),
                output_type=output_type,
                definition_id=f"agent-config-{node.content_digest[:24]}",
                capabilities=tuple(capabilities),
                plugins=plugins,
                subagents=tuple(child_definitions),
                model_recovery=ModelRecoveryPolicy(enabled=True),
            )
        except AgentDefinitionReconstructionError:
            raise
        except HarnessError as error:
            raise AgentDefinitionReconstructionError("harness_definition_invalid") from error
        except Exception as error:
            raise AgentDefinitionReconstructionError("agent_definition_invalid") from error

    def _provided_capabilities(
        self,
        node: AgentDefinitionReconstructionContext,
    ) -> tuple[AbstractCapability[AgentContext], ...]:
        if self._capability_provider is None:
            return ()
        try:
            capabilities = tuple(self._capability_provider(node))
        except Exception as error:
            raise AgentDefinitionReconstructionError("capability_provider_failed") from error
        if not all(isinstance(item, AbstractCapability) for item in capabilities):
            raise AgentDefinitionReconstructionError("capability_provider_invalid")
        return capabilities

    def _create_plugin(self, selection: PluginSelection) -> AbstractHarnessPlugin:
        try:
            return self._plugin_catalog.create_plugin(
                HarnessPluginFactoryContext(
                    plugin_key=selection.plugin_key,
                    plugin_id=selection.instance_name,
                    configuration=selection.config,
                    extensions={},
                )
            )
        except HarnessError as error:
            raise AgentDefinitionReconstructionError(
                "plugin_factory_failed",
                path=f"plugins.{selection.instance_name}",
            ) from error

    @staticmethod
    def _verify_effective_config(config: EffectiveAgentConfig) -> None:
        payload = config.model_dump(mode="json", by_alias=True, exclude={"content_digest"})
        if digest_request(payload) != config.content_digest:
            raise AgentDefinitionReconstructionError("effective_config_digest_mismatch")


def _output_type(spec: OutputSpec | None) -> Any:
    if spec is None or (spec.schema_ is None and spec.variants is None):
        return str
    try:
        if spec.schema_ is not None:
            schema = _inline_schema_resources(spec.schema_, spec.resources)
            return structured_output_type(schema, name=spec.name, description=spec.description)
        assert spec.variants is not None
        return tuple(
            structured_output_type(
                _inline_schema_resources(variant.schema_, variant.resources),
                name=variant.name,
                description=variant.description,
            )
            for variant in spec.variants
        )
    except AgentDefinitionReconstructionError:
        raise
    except Exception as error:
        raise AgentDefinitionReconstructionError("output_schema_invalid", path="output_spec") from error


def _permissions(config: EffectiveAgentConfig) -> ToolPermissions:
    rules = builtin_permission_rules(config.toolsets)

    def add(tool_id: str, permission: ToolPermissionSetting) -> None:
        if tool_id in rules:
            raise AgentDefinitionReconstructionError("tool_permission_conflict")
        rules[tool_id] = permission

    for selection in config.connection_tools:
        key = connection_source_key(selection.connection_id)
        if selection.tools is None:
            add(f"{source_tool_prefix(key, kind='mcp')}*", selection.permission)
            for name, permission in selection.permissions.items():
                add(source_tool_id(key, name, kind="mcp"), permission)
        else:
            for name in selection.tools:
                add(source_tool_id(key, name, kind="mcp"), selection.permissions.get(name, selection.permission))
    for tool in config.client_tools:
        add(source_tool_id(_CLIENT_TOOLSET_ID, tool.name), tool.permission)
    return ToolPermissions(rules=rules)


def _inline_schema_resources(
    schema: Mapping[str, Any],
    resources: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    try:
        root = DRAFT202012.create_resource(deepcopy(dict(schema)))
        registry = Registry().with_resources(
            (name, DRAFT202012.create_resource(deepcopy(dict(resource)))) for name, resource in resources.items()
        )
        resolver = registry.resolver_with_root(root)
        count = [0]
        expanded = _expand_schema_reference(
            root.contents,
            resolver=resolver,
            active_resource_ids=frozenset(),
            expansion_count=count,
        )
    except (CannotDetermineSpecification, Unresolvable) as error:
        raise AgentDefinitionReconstructionError("output_schema_reference_invalid", path="output_spec") from error
    if not isinstance(expanded, dict):  # pragma: no cover - validated schemas have mapping roots
        raise AgentDefinitionReconstructionError("output_schema_invalid", path="output_spec")
    return expanded


def _expand_schema_reference(
    value: Any,
    *,
    resolver: Any,
    active_resource_ids: frozenset[int],
    expansion_count: list[int],
) -> Any:
    if isinstance(value, list):
        return [
            _expand_schema_reference(
                item,
                resolver=resolver,
                active_resource_ids=active_resource_ids,
                expansion_count=expansion_count,
            )
            for item in value
        ]
    if not isinstance(value, Mapping):
        return deepcopy(value)

    reference = value.get("$ref")
    if isinstance(reference, str):
        expansion_count[0] += 1
        if expansion_count[0] > _MAX_SCHEMA_REFERENCE_EXPANSIONS:
            raise AgentDefinitionReconstructionError("output_schema_too_complex", path="output_spec")
        try:
            resolved = resolver.lookup(reference)
        except Unresolvable as error:
            raise AgentDefinitionReconstructionError(
                "output_schema_reference_invalid",
                path="output_spec",
            ) from error
        resource_id = id(resolved.contents)
        if resource_id in active_resource_ids:
            raise AgentDefinitionReconstructionError("output_schema_recursive", path="output_spec")
        target = _expand_schema_reference(
            resolved.contents,
            resolver=resolved.resolver,
            active_resource_ids=active_resource_ids | {resource_id},
            expansion_count=expansion_count,
        )
        siblings = {key: item for key, item in value.items() if key != "$ref"}
        if not siblings:
            return target
        sibling_schema = _expand_schema_reference(
            siblings,
            resolver=resolver,
            active_resource_ids=active_resource_ids,
            expansion_count=expansion_count,
        )
        combined: dict[str, Any] = {"allOf": [target, sibling_schema]}
        if isinstance(target, Mapping) and isinstance(target.get("type"), str):
            combined["type"] = target["type"]
        return combined

    resource = Resource.from_contents(dict(value), default_specification=DRAFT202012)
    nested_resolver = resolver.in_subresource(resource)
    return {
        key: _expand_schema_reference(
            item,
            resolver=nested_resolver,
            active_resource_ids=active_resource_ids,
            expansion_count=expansion_count,
        )
        for key, item in value.items()
    }


__all__ = [
    "AgentDefinitionCapabilityProvider",
    "AgentDefinitionReconstructionContext",
    "AgentDefinitionReconstructionError",
    "AgentReconstructor",
]
