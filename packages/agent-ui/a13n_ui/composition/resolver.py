"""Exact generation-captured Agent and Environment snapshot resolution."""

from __future__ import annotations

from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast

from a13n_environment_provider import (
    EnvironmentProviderError,
    EnvironmentProviderFactoryRegistration,
    EnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
)
from pydantic import JsonValue, ValidationError

from a13n_ui.configuration import (
    AgentDefinitionDocument,
    CatalogRepository,
    ConfigurationGeneration,
    ConfigurationSettings,
    DependencyLock,
    EnvironmentDefinitionDocument,
    ModelDefinition,
    PluginInstanceDefinition,
    PromptDefinition,
    ResourceKind,
    ResourceRevision,
    ResourceRevisionRef,
    canonical_digest,
    canonical_json_value,
)
from a13n_ui.configuration.models import ResolvedSkillRevisionContent
from a13n_ui.errors import CompositionError

from .models import (
    AgentEnvironmentCompatibility,
    ResolvedAgentNode,
    ResolvedAgentSnapshot,
    ResolvedEnvironmentBinding,
    ResolvedEnvironmentSnapshot,
    ResolvedModel,
    ResolvedPlugin,
    ResolvedPrompt,
    ResolvedSkill,
    ResolvedSubagentEdge,
)

_OPERATION_FAMILIES = frozenset({"files", "shell", "processes", "ports", "outputs", "state"})


class SnapshotResolver:
    """Resolve only exact revisions captured by one accepted generation."""

    def __init__(self, catalog: CatalogRepository, *, data_root: Path) -> None:
        self._catalog = catalog
        self._data_root = data_root.resolve(strict=False)

    async def resolve_agent(
        self,
        generation: ConfigurationGeneration,
        agent_id: str,
    ) -> ResolvedAgentSnapshot:
        resources = await self._load_generation(generation)
        root = _select(resources, ResourceKind.agent, agent_id)
        nodes: dict[ResourceRevisionRef, ResolvedAgentNode] = {}
        active: list[ResourceRevisionRef] = []

        async def resolve(reference: ResourceRevisionRef) -> ResolvedAgentNode:
            known = nodes.get(reference)
            if known is not None:
                return known
            if reference in active:
                raise CompositionError(
                    "Agent child definitions must form a finite acyclic graph.",
                    code="agent_snapshot_cycle",
                    details={"agent_id": reference.resource_id},
                )
            active.append(reference)
            try:
                revision = resources[reference]
                document = _parse(revision, AgentDefinitionDocument)
                model_revision = resources[_resolve_ref(resources, document.model)]
                prompt_revision = resources[_resolve_ref(resources, document.prompt)]
                model = _parse(model_revision, ModelDefinition)
                prompt = _parse(prompt_revision, PromptDefinition)
                model_lock = _one_lock(
                    model_revision,
                    dependency_kind="model_adapter",
                    key=model.provider_key,
                )

                plugins: list[ResolvedPlugin] = []
                for selected in document.plugins:
                    plugin_revision = resources[_resolve_ref(resources, selected)]
                    plugin = _parse(plugin_revision, PluginInstanceDefinition)
                    plugins.append(
                        ResolvedPlugin(
                            revision=plugin_revision.ref,
                            definition=plugin,
                            dependency=_one_lock(
                                plugin_revision,
                                dependency_kind="harness_plugin",
                                key=plugin.plugin_key,
                            ),
                        )
                    )

                skills: list[ResolvedSkill] = []
                for selected in document.skills.available:
                    skill_revision = resources[_resolve_ref(resources, selected)]
                    skill = _parse(skill_revision, ResolvedSkillRevisionContent)
                    package = await self._catalog.skill_package_reference(skill_revision.ref)
                    skills.append(
                        ResolvedSkill(
                            revision=skill_revision.ref,
                            definition=skill,
                            package_object_digest=package.logical_digest,
                        )
                    )
                skill_names = tuple(skill.definition.skill_name for skill in skills)
                if len(skill_names) != len(set(skill_names)):
                    raise CompositionError(
                        "An Agent selects ambiguous managed Skill names.",
                        code="agent_skill_ambiguous",
                        details={"agent_id": document.agent_id},
                    )
                if skills and document.skills.materialization_binding is None:
                    raise CompositionError(
                        "An Agent with available Skills requires a materialization binding.",
                        code="agent_skill_binding_missing",
                        details={"agent_id": document.agent_id},
                    )
                if not skills and document.skills.materialization_binding is not None:
                    raise CompositionError(
                        "An Agent without available Skills cannot select a materialization binding.",
                        code="agent_skill_binding_invalid",
                        details={"agent_id": document.agent_id},
                    )
                default_names: tuple[str, ...] | None
                if document.skills.default_selection.mode == "all":
                    default_names = None
                else:
                    default_names = document.skills.default_selection.names
                    unknown = sorted(set(default_names) - set(skill_names))
                    if unknown:
                        raise CompositionError(
                            "An exact Skill selection names an unavailable managed Skill.",
                            code="agent_skill_selection_missing",
                            details={"agent_id": document.agent_id, "skill_name": unknown[0]},
                        )

                _validate_requirements(document)
                if document.async_subagents.tools == "standard":
                    raise CompositionError(
                        "The async-subagent definition Capability is not installed in this release.",
                        code="capability_schema_unavailable",
                        details={"agent_id": document.agent_id},
                    )
                children: list[ResolvedSubagentEdge] = []
                for edge in document.subagents:
                    child_ref = _resolve_ref(resources, edge.agent)
                    await resolve(child_ref)
                    children.append(
                        ResolvedSubagentEdge(
                            name=edge.name,
                            description=edge.description,
                            target_agent=child_ref,
                            context=edge.context,
                            usage_limits=edge.usage_limits,
                            environment=edge.environment,
                            lifetime=edge.lifetime,
                            steering=edge.steering,
                            continuation=edge.continuation,
                        )
                    )
                node = ResolvedAgentNode(
                    agent_revision=revision.ref,
                    agent_id=document.agent_id,
                    display_name=document.display_name,
                    description=document.description,
                    model=ResolvedModel(
                        revision=model_revision.ref,
                        definition=model,
                        dependency=model_lock,
                    ),
                    prompt=ResolvedPrompt(revision=prompt_revision.ref, definition=prompt),
                    plugins=tuple(plugins),
                    skills=tuple(sorted(skills, key=lambda item: item.definition.skill_name)),
                    skill_materialization_binding=document.skills.materialization_binding,
                    default_skill_names=default_names,
                    capabilities=document.capabilities,
                    environment=document.environment,
                    subagents=tuple(children),
                    async_subagents=document.async_subagents,
                    output=document.output,
                    model_recovery=document.model_recovery,
                )
                nodes[reference] = node
                return node
            finally:
                active.pop()

        await resolve(root.ref)
        resolved_nodes = tuple(sorted(nodes.values(), key=lambda item: item.agent_id))
        locks = _unique_locks(
            lock
            for node in resolved_nodes
            for lock in (
                node.model.dependency,
                *(plugin.dependency for plugin in node.plugins),
            )
        )
        behavior = {
            "snapshot_schema_version": "1",
            "root_agent": root.ref,
            "resolved_agents": resolved_nodes,
            "adapter_locks": locks,
            "harness_release": _package_version("a13n-harness"),
        }
        return ResolvedAgentSnapshot(
            generation_id=generation.generation_id,
            catalog_digest=generation.catalog_digest,
            root_agent=root.ref,
            resolved_agents=resolved_nodes,
            adapter_locks=locks,
            harness_release=cast(str, behavior["harness_release"]),
            logical_agent_digest=canonical_digest(behavior),
        )

    async def resolve_environment(
        self,
        generation: ConfigurationGeneration,
        environment_id: str,
        settings: ConfigurationSettings,
    ) -> ResolvedEnvironmentSnapshot:
        resources = await self._load_generation(generation)
        revision = _select(resources, ResourceKind.environment, environment_id)
        document = _parse(revision, EnvironmentDefinitionDocument)
        try:
            factories = build_environment_provider_factory_catalog(
                builtin_keys=settings.builtin_provider_keys,
                extension_keys=settings.extension_provider_keys,
            )
        except EnvironmentProviderError as exc:
            raise CompositionError(
                "The selected Environment provider catalog is unavailable.",
                code=exc.code,
            ) from exc

        registrations = {item.provider_key: item for item in factories.registrations}
        bindings: list[ResolvedEnvironmentBinding] = []
        for binding in document.bindings:
            lock = _one_lock(
                revision,
                dependency_kind="environment_provider",
                key=binding.provider_key,
            )
            registration = registrations.get(binding.provider_key)
            if registration is None or not _provider_registration_matches(lock, registration):
                raise CompositionError(
                    "A locked Environment provider factory changed after generation acceptance.",
                    code="provider_factory_lock_mismatch",
                    details={"provider_key": binding.provider_key},
                )
            parameters = _provider_parameters(binding.provider_key, binding.provider_parameters, settings)
            spec = EnvironmentProviderSpec(
                provider_key=binding.provider_key,
                schema_version=binding.provider_schema_version,
                parameters=parameters,
            )
            try:
                resolved = factories.resolve_spec(spec)
            except EnvironmentProviderError as exc:
                raise CompositionError(
                    "An Environment binding selects an invalid provider specification.",
                    code=exc.code,
                    details={"binding_name": binding.binding_name},
                ) from exc
            normalized = cast(
                dict[str, JsonValue],
                canonical_json_value(resolved.configuration),
            )
            _reject_data_root_overlap(normalized, self._data_root)
            bindings.append(
                ResolvedEnvironmentBinding(
                    binding_name=binding.binding_name,
                    model_alias=binding.model_alias,
                    provider_key=binding.provider_key,
                    provider_schema_version=binding.provider_schema_version,
                    normalized_parameters=normalized,
                    permission_ceiling=binding.permission_ceiling,
                    required=binding.required,
                    dependency=lock,
                )
            )
        locks = _unique_locks(binding.dependency for binding in bindings)
        behavior = {
            "snapshot_schema_version": "1",
            "environment_revision": revision.ref,
            "definition": document,
            "bindings": tuple(bindings),
            "provider_locks": locks,
        }
        return ResolvedEnvironmentSnapshot(
            generation_id=generation.generation_id,
            catalog_digest=generation.catalog_digest,
            environment_revision=revision.ref,
            definition=document,
            bindings=tuple(bindings),
            provider_locks=locks,
            logical_environment_digest=canonical_digest(behavior),
        )

    async def _load_generation(
        self,
        generation: ConfigurationGeneration,
    ) -> dict[ResourceRevisionRef, ResourceRevision]:
        resources: dict[ResourceRevisionRef, ResourceRevision] = {}
        for reference in generation.resources:
            revision = await self._catalog.resource(reference)
            resources[reference] = revision
        return resources


def validate_compatibility(
    agent: ResolvedAgentSnapshot,
    environment: ResolvedEnvironmentSnapshot,
) -> AgentEnvironmentCompatibility:
    bindings = {binding.binding_name: binding for binding in environment.bindings}
    nodes = {node.agent_revision: node for node in agent.resolved_agents}
    for node in agent.resolved_agents:
        requirements = {item.binding_name: item for item in node.environment.bindings}
        if node.skill_materialization_binding is not None:
            requirement = requirements.get(node.skill_materialization_binding)
            if requirement is None or "files" not in requirement.required_operations:
                raise CompositionError(
                    "A Skill materialization binding must require the files operation family.",
                    code="agent_environment_incompatible",
                    details={"agent_id": node.agent_id},
                )
        for requirement in requirements.values():
            binding = bindings.get(requirement.binding_name)
            if binding is None:
                raise CompositionError(
                    "The Environment omits a required Agent binding.",
                    code="agent_environment_incompatible",
                    details={"agent_id": node.agent_id, "binding_name": requirement.binding_name},
                )
            available = _permission_families(binding.permission_ceiling)
            if not requirement.required_operations.issubset(available):
                raise CompositionError(
                    "An Environment binding permission ceiling does not satisfy the Agent.",
                    code="agent_environment_incompatible",
                    details={"agent_id": node.agent_id, "binding_name": requirement.binding_name},
                )
        for edge in node.subagents:
            child = nodes[edge.target_agent]
            selected = set(bindings if edge.environment.bindings is None else edge.environment.bindings)
            if not selected.issubset(bindings):
                raise CompositionError(
                    "A child Environment policy selects an unknown binding.",
                    code="agent_environment_incompatible",
                    details={"agent_id": node.agent_id, "child_name": edge.name},
                )
            child_required = {requirement.binding_name for requirement in child.environment.bindings}
            if edge.environment.mode == "none" and (child_required or child.skill_materialization_binding is not None):
                raise CompositionError(
                    "A child with Environment requirements cannot use the none policy.",
                    code="agent_environment_incompatible",
                    details={"agent_id": node.agent_id, "child_name": edge.name},
                )
            if edge.environment.mode != "none" and not child_required.issubset(selected):
                raise CompositionError(
                    "A child Environment policy omits a required binding.",
                    code="agent_environment_incompatible",
                    details={"agent_id": node.agent_id, "child_name": edge.name},
                )
    return AgentEnvironmentCompatibility(
        agent_snapshot_digest=agent.logical_agent_digest,
        environment_snapshot_digest=environment.logical_environment_digest,
    )


def _validate_requirements(document: AgentDefinitionDocument) -> None:
    for requirement in document.environment.bindings:
        unsupported = sorted(set(requirement.required_operations) - _OPERATION_FAMILIES)
        if unsupported:
            raise CompositionError(
                "An Agent requires an unknown Environment operation family.",
                code="agent_environment_requirement_invalid",
                details={"agent_id": document.agent_id, "operation": unsupported[0]},
            )


def _permission_families(permissions: frozenset[str]) -> frozenset[str]:
    return frozenset(
        family
        for family in _OPERATION_FAMILIES
        if family in permissions or any(value.startswith(f"{family}.") for value in permissions)
    )


def _reject_data_root_overlap(value: JsonValue, data_root: Path, *, key: str | None = None) -> None:
    if isinstance(value, dict):
        for name, item in value.items():
            _reject_data_root_overlap(item, data_root, key=name)
        return
    if isinstance(value, list):
        for item in value:
            _reject_data_root_overlap(item, data_root, key=key)
        return
    if not isinstance(value, str) or key not in {"path", "root", "host_path", "source"}:
        return
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        return
    resolved = candidate.resolve(strict=False)
    if resolved == data_root or resolved in data_root.parents or data_root in resolved.parents:
        raise CompositionError(
            "An executable Environment path overlaps the Agent UI data root.",
            code="environment_data_root_overlap",
        )


def _select(
    resources: Mapping[ResourceRevisionRef, ResourceRevision],
    kind: ResourceKind,
    resource_id: str,
) -> ResourceRevision:
    matches = [revision for ref, revision in resources.items() if ref.kind is kind and ref.resource_id == resource_id]
    if len(matches) != 1:
        raise CompositionError(
            "The selected resource is absent from the captured generation.",
            code="composition_resource_missing",
            details={"resource_kind": kind.value, "resource_id": resource_id},
        )
    return matches[0]


def _resolve_ref(
    resources: Mapping[ResourceRevisionRef, ResourceRevision],
    selected: object,
) -> ResourceRevisionRef:
    from a13n_ui.configuration import ResourceRef

    if not isinstance(selected, ResourceRef):
        raise TypeError("selected resource must be a ResourceRef")
    return _select(resources, selected.kind, selected.resource_id).ref


def _parse[T](revision: ResourceRevision, model_type: type[T]) -> T:
    try:
        return model_type.model_validate(revision.normalized_content, strict=True)  # type: ignore[attr-defined, no-any-return]
    except ValidationError as exc:
        raise CompositionError(
            "A selected resource revision is incompatible with snapshot resolution.",
            code="composition_resource_invalid",
            details={
                "resource_kind": revision.ref.kind.value,
                "resource_id": revision.ref.resource_id,
                "validation_error_count": exc.error_count(),
            },
        ) from exc


def _one_lock(
    revision: ResourceRevision,
    *,
    dependency_kind: str,
    key: str,
) -> DependencyLock:
    matches = {
        (
            lock.dependency_kind,
            lock.key,
            lock.distribution_name,
            lock.distribution_version,
        ): lock
        for lock in revision.dependency_provenance
        if lock.dependency_kind == dependency_kind and lock.key == key
    }
    if len(matches) != 1:
        raise CompositionError(
            "A selected resource revision has no exact dependency lock.",
            code="composition_dependency_missing",
            details={"resource_id": revision.ref.resource_id, "dependency_key": key},
        )
    return next(iter(matches.values()))


def _provider_parameters(
    provider_key: str,
    parameters: Mapping[str, JsonValue],
    settings: ConfigurationSettings,
) -> dict[str, JsonValue]:
    normalized = dict(parameters)
    if provider_key != "a13n.direct-local":
        return normalized
    root = normalized.get("root")
    directory_id = root.get("directory_id") if isinstance(root, dict) else None
    directories = {item.directory_id: item.path for item in settings.local_directories}
    path = directories.get(directory_id) if isinstance(directory_id, str) else None
    if path is None or not isinstance(root, dict):
        raise CompositionError(
            "A Direct Local Environment root selects an unavailable directory alias.",
            code="provider_spec_invalid",
        )
    normalized["root"] = {
        "path": str(path),
        "read_only": bool(root.get("read_only", False)),
    }
    return normalized


def _unique_locks(values: object) -> tuple[DependencyLock, ...]:
    locks = tuple(cast(object, values))  # type: ignore[arg-type]
    by_identity: dict[tuple[str, str, str | None, str | None], DependencyLock] = {}
    for value in locks:
        if not isinstance(value, DependencyLock):
            raise TypeError("dependency lock collection contains an invalid value")
        identity = (
            value.dependency_kind,
            value.key,
            value.distribution_name,
            value.distribution_version,
        )
        by_identity[identity] = value
    return tuple(by_identity[key] for key in sorted(by_identity))


def _provider_registration_matches(
    lock: DependencyLock,
    registration: EnvironmentProviderFactoryRegistration,
) -> bool:
    distribution_name = registration.distribution_name
    distribution_version = registration.distribution_version
    if distribution_name is None and lock.distribution_name == "a13n-environment-provider":
        distribution_name = "a13n-environment-provider"
        distribution_version = _package_version(distribution_name)
    return (lock.distribution_name is None or lock.distribution_name == distribution_name) and (
        lock.distribution_version is None or lock.distribution_version == distribution_version
    )


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError as exc:
        raise CompositionError(
            "The required runtime distribution is unavailable.",
            code="composition_dependency_missing",
            details={"distribution": distribution},
        ) from exc


__all__ = ["SnapshotResolver", "validate_compatibility"]
