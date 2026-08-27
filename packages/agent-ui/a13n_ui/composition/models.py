"""Authority-neutral immutable Agent and Environment snapshot contracts."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from a13n_ui.configuration.models import (
    AgentEnvironmentRequirements,
    AgentOutputSelection,
    AsyncSubagentConfiguration,
    ChildEnvironmentPolicy,
    DelegationContextSelection,
    DependencyLock,
    EnvironmentDefinitionDocument,
    FirstPartyCapabilitySelection,
    ModelDefinition,
    ModelRecoverySelection,
    PluginInstanceDefinition,
    PromptDefinition,
    ResolvedSkillRevisionContent,
    ResourceRevisionRef,
    StrictModel,
    UsageLimitsSelection,
    canonical_digest,
)

_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_ID = Annotated[str, Field(min_length=3, max_length=128, pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")]


class ResolvedModel(StrictModel):
    revision: ResourceRevisionRef
    definition: ModelDefinition
    dependency: DependencyLock


class ResolvedPrompt(StrictModel):
    revision: ResourceRevisionRef
    definition: PromptDefinition


class ResolvedPlugin(StrictModel):
    revision: ResourceRevisionRef
    definition: PluginInstanceDefinition
    dependency: DependencyLock


class ResolvedSkill(StrictModel):
    revision: ResourceRevisionRef
    definition: ResolvedSkillRevisionContent
    package_object_digest: _DIGEST


class ResolvedSubagentEdge(StrictModel):
    name: _ID
    description: str = Field(min_length=1, max_length=16 * 1024)
    target_agent: ResourceRevisionRef
    context: DelegationContextSelection
    usage_limits: UsageLimitsSelection | None
    environment: ChildEnvironmentPolicy
    lifetime: Literal["parent_scope", "session"]
    steering: Literal["enabled", "disabled"]
    continuation: Literal["enabled", "disabled"]


class ResolvedAgentNode(StrictModel):
    agent_revision: ResourceRevisionRef
    agent_id: _ID
    display_name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=16 * 1024)
    model: ResolvedModel
    prompt: ResolvedPrompt
    plugins: tuple[ResolvedPlugin, ...] = ()
    skills: tuple[ResolvedSkill, ...] = ()
    skill_materialization_binding: _ID | None = None
    default_skill_names: tuple[str, ...] | None = None
    capabilities: tuple[FirstPartyCapabilitySelection, ...] = ()
    environment: AgentEnvironmentRequirements
    subagents: tuple[ResolvedSubagentEdge, ...] = ()
    async_subagents: AsyncSubagentConfiguration
    output: AgentOutputSelection
    model_recovery: ModelRecoverySelection

    @model_validator(mode="after")
    def _consistent_skills(self) -> Self:
        if bool(self.skills) != (self.skill_materialization_binding is not None):
            raise ValueError("Skill materialization binding is present exactly when Skills are available")
        names = tuple(skill.definition.skill_name for skill in self.skills)
        if len(names) != len(set(names)):
            raise ValueError("resolved Skill names must be unique within one Agent")
        if self.default_skill_names is not None:
            if len(self.default_skill_names) != len(set(self.default_skill_names)):
                raise ValueError("resolved default Skill names must be unique")
            if not set(self.default_skill_names).issubset(names):
                raise ValueError("resolved default Skill names must select available Skills")
        return self


class ResolvedAgentSnapshot(StrictModel):
    snapshot_schema_version: Literal["1"] = "1"
    generation_id: _ID
    catalog_digest: _DIGEST
    root_agent: ResourceRevisionRef
    logical_agent_digest: _DIGEST
    resolved_agents: tuple[ResolvedAgentNode, ...] = Field(min_length=1, max_length=10_000)
    adapter_locks: tuple[DependencyLock, ...] = ()
    harness_release: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def _validate_snapshot(self) -> Self:
        refs = tuple(node.agent_revision for node in self.resolved_agents)
        agent_ids = tuple(node.agent_id for node in self.resolved_agents)
        if len(refs) != len(set(refs)) or self.root_agent not in refs:
            raise ValueError("resolved Agent nodes must be unique and include the root")
        if len(agent_ids) != len(set(agent_ids)):
            raise ValueError("resolved Agent IDs must be unique")
        known = set(refs)
        expected_locks = {
            _lock_identity(dependency)
            for node in self.resolved_agents
            for dependency in (
                node.model.dependency,
                *(plugin.dependency for plugin in node.plugins),
            )
        }
        actual_locks = tuple(_lock_identity(lock) for lock in self.adapter_locks)
        if (
            len(actual_locks) != len(set(actual_locks))
            or set(actual_locks) != expected_locks
            or any(lock.dependency_kind not in {"model_adapter", "harness_plugin"} for lock in self.adapter_locks)
        ):
            raise ValueError("adapter locks must exactly cover resolved Agent dependencies")
        edges = {
            node.agent_revision: tuple(edge.target_agent for edge in node.subagents) for node in self.resolved_agents
        }
        if any(target not in known for targets in edges.values() for target in targets):
            raise ValueError("resolved child edges must select included Agent nodes")
        _require_acyclic(self.root_agent, edges)
        expected = canonical_digest(
            self.model_dump(
                mode="python",
                exclude={"logical_agent_digest", "generation_id", "catalog_digest"},
            )
        )
        if self.logical_agent_digest != expected:
            raise ValueError("logical Agent digest does not match snapshot content")
        return self


class ResolvedEnvironmentLifecycleCapabilities(StrictModel):
    pause_modes: frozenset[Literal["full", "filesystem"]] = frozenset()
    resource_allocation: Literal["single_from_spec", "multiple_from_spec"]
    attachment_concurrency: Literal["single", "shared"]


class ResolvedEnvironmentBinding(StrictModel):
    binding_name: _ID
    model_alias: str = Field(min_length=1, max_length=63)
    provider_key: str = Field(min_length=3, max_length=128)
    provider_schema_version: str = Field(min_length=1, max_length=64)
    normalized_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    permission_ceiling: frozenset[str] = frozenset()
    required: bool = True
    lifecycle_capabilities: ResolvedEnvironmentLifecycleCapabilities
    dependency: DependencyLock


class ResolvedEnvironmentSnapshot(StrictModel):
    snapshot_schema_version: Literal["1"] = "1"
    generation_id: _ID
    catalog_digest: _DIGEST
    environment_revision: ResourceRevisionRef
    logical_environment_digest: _DIGEST
    definition: EnvironmentDefinitionDocument
    bindings: tuple[ResolvedEnvironmentBinding, ...]
    provider_locks: tuple[DependencyLock, ...] = ()

    @model_validator(mode="after")
    def _validate_snapshot(self) -> Self:
        names = tuple(binding.binding_name for binding in self.bindings)
        if len(names) != len(set(names)):
            raise ValueError("resolved Environment binding names must be unique")
        expected_locks = {_lock_identity(binding.dependency) for binding in self.bindings}
        actual_locks = tuple(_lock_identity(lock) for lock in self.provider_locks)
        if (
            len(actual_locks) != len(set(actual_locks))
            or set(actual_locks) != expected_locks
            or any(lock.dependency_kind != "environment_provider" for lock in self.provider_locks)
        ):
            raise ValueError("provider locks must exactly cover resolved Environment bindings")
        expected = canonical_digest(
            self.model_dump(
                mode="python",
                exclude={"logical_environment_digest", "generation_id", "catalog_digest"},
            )
        )
        if self.logical_environment_digest != expected:
            raise ValueError("logical Environment digest does not match snapshot content")
        return self


def _lock_identity(lock: DependencyLock) -> tuple[str, str, str | None, str | None]:
    return (
        lock.dependency_kind,
        lock.key,
        lock.distribution_name,
        lock.distribution_version,
    )


def _require_acyclic(
    root: ResourceRevisionRef,
    edges: dict[ResourceRevisionRef, tuple[ResourceRevisionRef, ...]],
) -> None:
    visited: set[ResourceRevisionRef] = set()
    active: set[ResourceRevisionRef] = set()

    def visit(reference: ResourceRevisionRef) -> None:
        if reference in active:
            raise ValueError("resolved Agent child graph must be acyclic")
        if reference in visited:
            return
        active.add(reference)
        for target in edges[reference]:
            visit(target)
        active.remove(reference)
        visited.add(reference)

    visit(root)
    if visited != set(edges):
        raise ValueError("resolved Agent nodes must be reachable from the root")


class SnapshotReference(StrictModel):
    snapshot_kind: Literal["agent", "environment"]
    logical_digest: _DIGEST
    object_digest: _DIGEST
    generation_id: _ID
    root_revision: ResourceRevisionRef


class AgentEnvironmentCompatibility(StrictModel):
    agent_snapshot_digest: _DIGEST
    environment_snapshot_digest: _DIGEST
    compatible: Literal[True] = True


__all__ = [
    "AgentEnvironmentCompatibility",
    "ResolvedAgentNode",
    "ResolvedAgentSnapshot",
    "ResolvedEnvironmentBinding",
    "ResolvedEnvironmentLifecycleCapabilities",
    "ResolvedEnvironmentSnapshot",
    "ResolvedModel",
    "ResolvedPlugin",
    "ResolvedPrompt",
    "ResolvedSkill",
    "ResolvedSubagentEdge",
    "SnapshotReference",
]
