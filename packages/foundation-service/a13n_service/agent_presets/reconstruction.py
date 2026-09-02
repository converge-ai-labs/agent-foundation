"""Trusted reconstruction of frozen AgentPreset configuration into Harness values."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Protocol

from a13n_harness import (
    AgentContext,
    AgentDefinition,
    AgentSpec,
    SubagentDefinition,
)
from a13n_harness import (
    DelegationContextPolicy as HarnessDelegationContextPolicy,
)
from a13n_harness.capabilities import SubagentCapability, SubagentOperator
from a13n_harness.errors import HarnessError
from a13n_harness.plugin_factories import (
    HarnessPluginFactoryCatalog,
    HarnessPluginFactoryContext,
    HarnessPluginFactoryRegistration,
)
from a13n_harness.plugins import AbstractHarnessPlugin
from a13n_harness.tools.client import (
    ClientToolDefinition,
    ClientToolsCapability,
    ClientToolsetDefinition,
    ClientToolsSpec,
)
from packaging.utils import canonicalize_name
from pydantic_ai.agent.abstract import AgentRetries
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.output import StructuredDict
from referencing import Registry, Resource
from referencing.exceptions import CannotDetermineSpecification, Unresolvable
from referencing.jsonschema import DRAFT202012

from .domain import (
    AgentPresetRevision,
    AssetPublicationConfig,
    EffectiveAgentConfig,
    EnvironmentExecutionConfig,
    InputAdapterConfig,
    OutputSpec,
    ProtocolConfig,
    ResolvedAgentModelConfig,
    ResolvedConnectorSelection,
    ResolvedPluginVersion,
    ResolvedRevisionContent,
    ResolvedSkillSelection,
    ResolvedSubagentEdge,
    RetryConfig,
    SecretRequirement,
    canonical_digest,
)
from .resolution import MAX_SUBAGENT_DEPTH, MAX_SUBAGENT_NODES

_CLIENT_TOOLSET_ID = "foundation"
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

    agent_preset_id: str
    agent_preset_revision_id: str
    content_digest: str
    is_root: bool
    input_adapter: InputAdapterConfig
    resolved_skills: tuple[ResolvedSkillSelection, ...]
    resolved_connectors: tuple[ResolvedConnectorSelection, ...]
    resolved_environment: EnvironmentExecutionConfig | None
    secret_requirements: tuple[SecretRequirement, ...]
    asset_publication: AssetPublicationConfig | None
    protocol: ProtocolConfig


class AgentDefinitionCapabilityProvider(Protocol):
    """Construct fresh trusted definition Capabilities for one frozen graph node."""

    def __call__(
        self,
        context: AgentDefinitionReconstructionContext,
    ) -> Sequence[AbstractCapability[AgentContext]]: ...


@dataclass(frozen=True, slots=True)
class _NodeSnapshot:
    agent_preset_id: str
    agent_preset_revision_id: str
    content_digest: str
    resolved_model: ResolvedAgentModelConfig
    resolved_plugin_versions: tuple[ResolvedPluginVersion, ...]
    resolved_skills: tuple[ResolvedSkillSelection, ...]
    resolved_connectors: tuple[ResolvedConnectorSelection, ...]
    resolved_environment: EnvironmentExecutionConfig | None
    resolved_subagents: tuple[ResolvedSubagentEdge, ...]
    instructions: str
    input_adapter: InputAdapterConfig
    client_tools: tuple[ClientToolDefinition, ...]
    output_spec: OutputSpec | None
    retries: RetryConfig | None
    secret_requirements: tuple[SecretRequirement, ...]
    asset_publication: AssetPublicationConfig | None
    protocol: ProtocolConfig


class AgentPresetReconstructor:
    """Rebuild exact process-local Harness definitions without mutable lookups or I/O."""

    def __init__(
        self,
        plugin_catalog: HarnessPluginFactoryCatalog,
        *,
        capability_provider: AgentDefinitionCapabilityProvider | None = None,
    ) -> None:
        if not isinstance(plugin_catalog, HarnessPluginFactoryCatalog):
            raise TypeError("plugin_catalog must be a HarnessPluginFactoryCatalog")
        self._plugin_catalog = plugin_catalog
        self._capability_provider = capability_provider
        self._registrations = {item.plugin_key: item for item in plugin_catalog.registrations}

    def reconstruct(
        self,
        *,
        agent_preset_id: str,
        agent_preset_revision_id: str,
        effective_config: EffectiveAgentConfig,
        child_revisions: Mapping[str, AgentPresetRevision],
        subagent_operator: SubagentOperator | None = None,
    ) -> AgentDefinition[Any]:
        """Reconstruct the accepted root snapshot and its exact immutable child graph."""

        self._verify_effective_config(effective_config)
        children = dict(child_revisions)
        for revision_id, revision in children.items():
            if revision_id != revision.id:
                raise AgentDefinitionReconstructionError(
                    "subagent_revision_identity_mismatch",
                    path=f"child_revisions.{revision_id}",
                )
            self._verify_revision(revision)

        root = _snapshot_from_effective(
            agent_preset_id=agent_preset_id,
            agent_preset_revision_id=agent_preset_revision_id,
            effective=effective_config,
        )
        occurrence_count = [0]
        return self._definition(
            root,
            child_revisions=children,
            subagent_operator=subagent_operator,
            active_revision_ids=(),
            depth=0,
            occurrence_count=occurrence_count,
            is_root=True,
        )

    def _definition(
        self,
        node: _NodeSnapshot,
        *,
        child_revisions: Mapping[str, AgentPresetRevision],
        subagent_operator: SubagentOperator | None,
        active_revision_ids: tuple[str, ...],
        depth: int,
        occurrence_count: list[int],
        is_root: bool,
    ) -> AgentDefinition[Any]:
        if depth > MAX_SUBAGENT_DEPTH:
            raise AgentDefinitionReconstructionError("subagent_graph_too_deep")
        if node.agent_preset_revision_id in active_revision_ids:
            raise AgentDefinitionReconstructionError("subagent_cycle")
        occurrence_count[0] += 1
        if occurrence_count[0] > MAX_SUBAGENT_NODES:
            raise AgentDefinitionReconstructionError("subagent_graph_too_large")

        active_path = (*active_revision_ids, node.agent_preset_revision_id)
        child_definitions: list[SubagentDefinition] = []
        for edge in node.resolved_subagents:
            child = child_revisions.get(edge.child_agent_preset_revision_id)
            path = f"subagents.{edge.name}"
            if child is None:
                raise AgentDefinitionReconstructionError("subagent_revision_missing", path=path)
            if child.agent_preset_id != edge.child_agent_preset_id:
                raise AgentDefinitionReconstructionError("subagent_preset_mismatch", path=path)
            child_node = _snapshot_from_revision(child)
            child_definition = self._definition(
                child_node,
                child_revisions=child_revisions,
                subagent_operator=subagent_operator,
                active_revision_ids=active_path,
                depth=depth + 1,
                occurrence_count=occurrence_count,
                is_root=False,
            )
            child_definitions.append(
                SubagentDefinition(
                    name=edge.name,
                    description=(
                        edge.description
                        or child.config.protocol.public_description
                        or child.config.protocol.public_name
                    ),
                    agent=child_definition,
                    context=HarnessDelegationContextPolicy(
                        include_task=edge.context.include_task,
                        history=edge.context.history,
                        task_state=edge.context.task_state,
                    ),
                    usage_limits=edge.usage_limits,
                )
            )

        capabilities = list(self._provided_capabilities(node, is_root=is_root))
        if node.client_tools:
            capabilities.append(
                ClientToolsCapability(
                    spec=ClientToolsSpec(
                        default_toolsets=(
                            ClientToolsetDefinition(
                                toolset_id=_CLIENT_TOOLSET_ID,
                                tools=node.client_tools,
                            ),
                        ),
                        allow_run_override=False,
                    )
                )
            )
        if child_definitions:
            capabilities.append(
                SubagentCapability(
                    async_enabled=subagent_operator is not None,
                    operator=subagent_operator,
                )
            )

        try:
            plugins = tuple(self._create_plugin(selection) for selection in node.resolved_plugin_versions)
            output_type = _output_type(node.output_spec)
            retries = (
                None if node.retries is None else AgentRetries(tools=node.retries.tools, output=node.retries.output)
            )
            return AgentDefinition(
                agent=AgentSpec(
                    model=node.resolved_model.execution.model_id,
                    name=node.protocol.public_name,
                    description=node.protocol.public_description,
                    model_settings=dict(node.resolved_model.settings),
                    system_prompt=node.instructions,
                    retries=retries,
                    model_characteristics=node.resolved_model.characteristics,
                ),
                output_type=output_type,
                definition_id=f"agent-config-{node.content_digest[:24]}",
                capabilities=tuple(capabilities),
                plugins=plugins,
                subagents=tuple(child_definitions),
            )
        except AgentDefinitionReconstructionError:
            raise
        except HarnessError as error:
            raise AgentDefinitionReconstructionError("harness_definition_invalid") from error
        except Exception as error:
            raise AgentDefinitionReconstructionError("agent_definition_invalid") from error

    def _provided_capabilities(
        self,
        node: _NodeSnapshot,
        *,
        is_root: bool,
    ) -> tuple[AbstractCapability[AgentContext], ...]:
        if self._capability_provider is None:
            return ()
        context = AgentDefinitionReconstructionContext(
            agent_preset_id=node.agent_preset_id,
            agent_preset_revision_id=node.agent_preset_revision_id,
            content_digest=node.content_digest,
            is_root=is_root,
            input_adapter=node.input_adapter,
            resolved_skills=node.resolved_skills,
            resolved_connectors=node.resolved_connectors,
            resolved_environment=node.resolved_environment,
            secret_requirements=node.secret_requirements,
            asset_publication=node.asset_publication,
            protocol=node.protocol,
        )
        try:
            capabilities = tuple(self._capability_provider(context))
        except Exception as error:
            raise AgentDefinitionReconstructionError("capability_provider_failed") from error
        if not all(isinstance(item, AbstractCapability) for item in capabilities):
            raise AgentDefinitionReconstructionError("capability_provider_invalid")
        return capabilities

    def _create_plugin(self, selection: ResolvedPluginVersion) -> AbstractHarnessPlugin:
        registration = self._registrations.get(selection.plugin_key)
        if not _registration_matches(registration, selection):
            raise AgentDefinitionReconstructionError(
                "plugin_factory_provenance_mismatch",
                path=f"plugins.{selection.instance_name}",
            )
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
        if canonical_digest(payload) != config.content_digest:
            raise AgentDefinitionReconstructionError("effective_config_digest_mismatch")

    @staticmethod
    def _verify_revision(revision: AgentPresetRevision) -> None:
        resolved = ResolvedRevisionContent(
            resolved_model=revision.resolved_model,
            resolved_plugin_versions=revision.resolved_plugin_versions,
            runtime_lock_digest=revision.runtime_lock_digest,
            resolved_skills=revision.resolved_skills,
            resolved_connectors=revision.resolved_connectors,
            resolved_environment=revision.resolved_environment,
            resolved_subagents=revision.resolved_subagents,
        )
        payload = {
            "plugin_runtime_mode": revision.plugin_runtime_mode.value,
            "config": revision.config.model_dump(mode="json", by_alias=True),
            "resolved": resolved.model_dump(mode="json", by_alias=True),
        }
        if canonical_digest(payload) != revision.content_digest:
            raise AgentDefinitionReconstructionError(
                "subagent_revision_digest_mismatch",
                path=f"child_revisions.{revision.id}",
            )


def _snapshot_from_effective(
    *,
    agent_preset_id: str,
    agent_preset_revision_id: str,
    effective: EffectiveAgentConfig,
) -> _NodeSnapshot:
    return _NodeSnapshot(
        agent_preset_id=agent_preset_id,
        agent_preset_revision_id=agent_preset_revision_id,
        content_digest=effective.content_digest,
        resolved_model=effective.resolved_model,
        resolved_plugin_versions=effective.resolved_plugin_versions,
        resolved_skills=effective.resolved_skills,
        resolved_connectors=effective.resolved_connectors,
        resolved_environment=effective.resolved_environment,
        resolved_subagents=effective.resolved_subagents,
        instructions=effective.instructions,
        input_adapter=effective.input_adapter,
        client_tools=effective.client_tools,
        output_spec=effective.output_spec,
        retries=effective.retries,
        secret_requirements=effective.secret_requirements,
        asset_publication=effective.asset_publication,
        protocol=effective.protocol,
    )


def _snapshot_from_revision(revision: AgentPresetRevision) -> _NodeSnapshot:
    config = revision.config
    return _NodeSnapshot(
        agent_preset_id=revision.agent_preset_id,
        agent_preset_revision_id=revision.id,
        content_digest=revision.content_digest,
        resolved_model=revision.resolved_model,
        resolved_plugin_versions=revision.resolved_plugin_versions,
        resolved_skills=revision.resolved_skills,
        resolved_connectors=revision.resolved_connectors,
        resolved_environment=revision.resolved_environment,
        resolved_subagents=revision.resolved_subagents,
        instructions=config.instructions,
        input_adapter=config.input_adapter,
        client_tools=config.client_tools,
        output_spec=config.output_spec,
        retries=config.retries,
        secret_requirements=config.secret_requirements,
        asset_publication=config.asset_publication,
        protocol=config.protocol,
    )


def _registration_matches(
    registration: HarnessPluginFactoryRegistration | None,
    selection: ResolvedPluginVersion,
) -> bool:
    return bool(
        registration is not None
        and registration.distribution_name is not None
        and str(canonicalize_name(registration.distribution_name))
        == str(canonicalize_name(selection.distribution_name))
        and registration.distribution_version == selection.distribution_version
        and (
            registration.class_module == selection.top_level_package
            or registration.class_module.startswith(f"{selection.top_level_package}.")
        )
    )


def _output_type(spec: OutputSpec | None) -> Any:
    if spec is None or (spec.schema_ is None and spec.variants is None):
        return str
    try:
        if spec.schema_ is not None:
            schema = _inline_schema_resources(spec.schema_, spec.resources)
            return StructuredDict(schema, name=spec.name, description=spec.description)
        assert spec.variants is not None
        return tuple(
            StructuredDict(
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
    "AgentPresetReconstructor",
]
