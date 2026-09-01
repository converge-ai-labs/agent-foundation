"""Authority-neutral immutable Agent and Environment snapshot contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self, cast

from pydantic import Field, JsonValue, ValidationInfo, model_validator

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
    SubagentIdentitySelection,
    UsageLimitsSelection,
    _without_legacy_defaults,
    canonical_digest,
    legacy_environment_access,
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
    identity: SubagentIdentitySelection = SubagentIdentitySelection()
    usage_limits: UsageLimitsSelection | None
    environment: ChildEnvironmentPolicy

    @model_validator(mode="before")
    @classmethod
    def _legacy_runtime_controls(cls, value: object) -> object:
        return _without_legacy_defaults(
            value,
            {"lifetime": "parent_scope", "steering": "disabled", "continuation": "disabled"},
        )


class ResolvedAgentNode(StrictModel):
    agent_revision: ResourceRevisionRef
    agent_id: _ID
    display_name: str = Field(min_length=1, max_length=256)
    description: str | None = Field(default=None, max_length=16 * 1024)
    model: ResolvedModel
    prompt: ResolvedPrompt
    plugins: tuple[ResolvedPlugin, ...] = ()
    skills: tuple[ResolvedSkill, ...] = ()
    skill_materialization_mount: _ID | None = None
    default_skill_names: tuple[str, ...] | None = None
    capabilities: tuple[FirstPartyCapabilitySelection, ...] = ()
    environment_tools: bool = True
    environment: AgentEnvironmentRequirements
    subagents: tuple[ResolvedSubagentEdge, ...] = ()
    async_subagents: AsyncSubagentConfiguration
    output: AgentOutputSelection
    model_recovery: ModelRecoverySelection

    @model_validator(mode="after")
    def _consistent_skills(self) -> Self:
        if bool(self.skills) != (self.skill_materialization_mount is not None):
            raise ValueError("Skill materialization mount is present exactly when Skills are available")
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
    def _validate_snapshot(self, info: ValidationInfo) -> Self:
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
            legacy_identity_matches = False
            if not any(edge.identity.inherit_agent_id for node in self.resolved_agents for edge in node.subagents):
                legacy_content = cast(
                    dict[str, Any],
                    self.model_dump(
                        mode="python",
                        exclude={"logical_agent_digest", "generation_id", "catalog_digest"},
                    ),
                )
                for node in cast(tuple[dict[str, Any], ...], legacy_content["resolved_agents"]):
                    for edge in cast(tuple[dict[str, Any], ...], node["subagents"]):
                        edge.pop("identity")
                legacy_identity_matches = self.logical_agent_digest == canonical_digest(legacy_content)
            if not legacy_identity_matches and not _legacy_snapshot_digest_matches(
                info,
                field="logical_agent_digest",
                expected=self.logical_agent_digest,
            ):
                raise ValueError("logical Agent digest does not match snapshot content")
        return self


class ResolvedEnvironmentLifecycleCapabilities(StrictModel):
    pause_modes: frozenset[Literal["full", "filesystem"]] = frozenset()
    resource_allocation: Literal["single_from_spec", "multiple_from_spec"]
    attachment_concurrency: Literal["single", "shared"]


class ResolvedEnvironmentMountDefinition(StrictModel):
    mount_name: _ID
    model_alias: str = Field(min_length=1, max_length=63)
    provider_key: str = Field(min_length=3, max_length=128)
    provider_schema_version: str = Field(min_length=1, max_length=64)
    normalized_parameters: dict[str, JsonValue] = Field(default_factory=dict)
    access: Literal["read_only", "read_write", "full"] = "full"
    lifecycle_capabilities: ResolvedEnvironmentLifecycleCapabilities
    dependency: DependencyLock

    @model_validator(mode="before")
    @classmethod
    def _legacy_permission_ceiling(cls, value: object) -> object:
        if not isinstance(value, dict) or "permission_ceiling" not in value:
            return value
        normalized = dict(value)
        if "access" in normalized:
            raise ValueError("permission_ceiling and access must not both be present")
        normalized["access"] = legacy_environment_access(normalized.pop("permission_ceiling"))
        return normalized


class ResolvedEnvironmentSnapshot(StrictModel):
    snapshot_schema_version: Literal["1"] = "1"
    generation_id: _ID
    catalog_digest: _DIGEST
    environment_revision: ResourceRevisionRef
    logical_environment_digest: _DIGEST
    definition: EnvironmentDefinitionDocument
    mounts: tuple[ResolvedEnvironmentMountDefinition, ...]
    provider_locks: tuple[DependencyLock, ...] = ()

    @model_validator(mode="after")
    def _validate_snapshot(self, info: ValidationInfo) -> Self:
        names = tuple(mount.mount_name for mount in self.mounts)
        if len(names) != len(set(names)):
            raise ValueError("resolved Environment mount names must be unique")
        expected_locks = {_lock_identity(mount.dependency) for mount in self.mounts}
        actual_locks = tuple(_lock_identity(lock) for lock in self.provider_locks)
        if (
            len(actual_locks) != len(set(actual_locks))
            or set(actual_locks) != expected_locks
            or any(lock.dependency_kind != "environment_provider" for lock in self.provider_locks)
        ):
            raise ValueError("provider locks must exactly cover resolved Environment mounts")
        expected = canonical_digest(
            self.model_dump(
                mode="python",
                exclude={"logical_environment_digest", "generation_id", "catalog_digest"},
            )
        )
        if self.logical_environment_digest != expected and not _legacy_snapshot_digest_matches(
            info,
            field="logical_environment_digest",
            expected=self.logical_environment_digest,
        ):
            raise ValueError("logical Environment digest does not match snapshot content")
        return self


def _legacy_snapshot_digest_matches(info: ValidationInfo, *, field: str, expected: str) -> bool:
    if not isinstance(info.context, dict):
        return False
    payload = info.context.get("legacy_snapshot_payload")
    if not isinstance(payload, Mapping) or payload.get(field) != expected:
        return False
    content = dict(payload)
    for name in {field, "generation_id", "catalog_digest"}:
        content.pop(name, None)
    return canonical_digest(content) == expected


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
    "ResolvedEnvironmentLifecycleCapabilities",
    "ResolvedEnvironmentMountDefinition",
    "ResolvedEnvironmentSnapshot",
    "ResolvedModel",
    "ResolvedPlugin",
    "ResolvedPrompt",
    "ResolvedSkill",
    "ResolvedSubagentEdge",
    "SnapshotReference",
]
