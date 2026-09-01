"""Trusted process-local reconstruction from immutable Agent snapshots."""

from __future__ import annotations

import base64
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any
from uuid import uuid4

from a13n_harness import (
    AgentDefinition,
    AgentSpec,
    DelegationContextPolicy,
    ExecutableAgent,
    HarnessBuilder,
    ModelRecoveryPolicy,
    SubagentDefinition,
    SubagentIdentityPolicy,
)
from a13n_harness.capabilities import (
    DocumentsCapability,
    FileSkillSource,
    MediaCapability,
    ShellOperator,
    SkillManager,
    SkillsCapability,
    SkillsPolicy,
    SubagentCapability,
    SubagentOperator,
    UserInteractionCapability,
    WebCapability,
    WorkingStateCapability,
    WorkingStateConfiguration,
)
from a13n_harness.environment import (
    DynamicEnvironmentCapability,
    DynamicEnvironmentConfiguration,
)
from a13n_harness.environment.files import FileOperator, FileQueryRequest
from a13n_harness.environment.models import EnvironmentError
from a13n_harness.mcp import (
    ContextualMCP,
    MCPContextHeaderBinding,
    MCPContextHeaders,
    MCPContextHeadersConfig,
)
from a13n_harness.plugin_factories import (
    HarnessPluginFactoryContext,
    HarnessPluginFactoryRegistration,
    build_harness_plugin_factory_catalog,
)
from anyio import Lock
from pydantic import JsonValue
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.usage import UsageLimits

from a13n_ui.configuration import DependencyLock
from a13n_ui.errors import CompositionError
from a13n_ui.model_adapters import model_adapter_registration

from .models import (
    ResolvedAgentNode,
    ResolvedAgentSnapshot,
    ResolvedEnvironmentSnapshot,
    ResolvedSkill,
)


@dataclass(frozen=True, slots=True)
class _PackageFile:
    path: str
    content: bytes
    sha256: str


class SnapshotSkillMaterializer:
    """Materialize one immutable managed Skill set into its exact logical root."""

    def __init__(self, materializer_id: str, target_root: str, files: Mapping[str, tuple[_PackageFile, ...]]) -> None:
        self._materializer_id = materializer_id
        self._target_root = target_root
        self._files = {name: tuple(values) for name, values in files.items()}
        self._lock = Lock()

    @property
    def materializer_id(self) -> str:
        return self._materializer_id

    @property
    def target_root(self) -> str:
        return self._target_root

    async def materialize(self, *, files: FileOperator) -> None:
        async with self._lock:
            await self._materialize(files)

    async def _materialize(self, files: FileOperator) -> None:
        if await self._matches_target(files):
            return
        stage = f"{self.target_root}.stage-{uuid4().hex}"
        error: BaseException | None = None
        try:
            await files.mkdir(stage, parents=True, exist_ok=False)
            for skill_name, package in sorted(self._files.items()):
                root = f"{stage}/{skill_name}"
                await files.mkdir(root, parents=True, exist_ok=False)
                for item in package:
                    destination = f"{root}/{item.path}"
                    parent = destination.rsplit("/", 1)[0]
                    await files.mkdir(parent, parents=True, exist_ok=True)
                    await files.write_bytes_stream(
                        destination,
                        _one_chunk(item.content),
                        mode="create",
                    )
                    copied = await files.read_bytes(destination)
                    if hashlib.sha256(copied).hexdigest() != item.sha256:
                        raise CompositionError(
                            "A materialized Skill file failed digest verification.",
                            code="skill_materialization_mismatch",
                        )
            try:
                await files.move(stage, self.target_root, replace=True)
            except EnvironmentError:
                if not await self._matches_target(files):
                    raise
            if not await self._matches_target(files):
                raise CompositionError(
                    "The managed Skill target does not match its immutable package set after publication.",
                    code="skill_materialization_mismatch",
                )
        except BaseException as exc:
            error = exc
            raise
        finally:
            try:
                await files.stat(stage)
            except EnvironmentError as cleanup_error:
                if cleanup_error.code != "environment_not_found":
                    if error is None:
                        raise
                    error.add_note(f"Skill materialization cleanup failed with {cleanup_error.code}.")
            else:
                try:
                    await files.remove(stage, recursive=True)
                except EnvironmentError as cleanup_error:
                    if error is None:
                        raise
                    error.add_note(f"Skill materialization cleanup failed with {cleanup_error.code}.")

    async def _matches_target(self, files: FileOperator) -> bool:
        try:
            metadata = await files.stat(self.target_root)
        except EnvironmentError as exc:
            if exc.code == "environment_not_found":
                return False
            raise
        if metadata.kind != "directory":
            return False
        expected_entries: set[str] = set()
        expected_files: list[tuple[str, _PackageFile]] = []
        for skill_name, package in self._files.items():
            expected_entries.add(skill_name)
            for item in package:
                relative = f"{skill_name}/{item.path}"
                expected_entries.add(relative)
                parts = relative.split("/")
                expected_entries.update("/".join(parts[:index]) for index in range(1, len(parts)))
                expected_files.append((relative, item))
        try:
            result = await files.query(
                FileQueryRequest(
                    root=self.target_root,
                    pattern="**",
                    recursive=True,
                    include_hidden=True,
                    offset=0,
                    max_results=len(expected_entries) + 1,
                )
            )
        except EnvironmentError as exc:
            if exc.code == "environment_not_found":
                return False
            raise
        prefix = f"{self.target_root}/"
        actual = {entry.path.removeprefix(prefix) for entry in result.entries if entry.path != self.target_root}
        if result.has_more or actual != expected_entries:
            return False
        try:
            for relative, item in expected_files:
                copied = await files.read_bytes(f"{self.target_root}/{relative}")
                if hashlib.sha256(copied).hexdigest() != item.sha256:
                    return False
        except EnvironmentError as exc:
            if exc.code == "environment_not_found":
                return False
            raise
        return True


async def _one_chunk(content: bytes) -> AsyncIterator[bytes]:
    yield content


class ExecutableCache:
    """Process-local digest-keyed executable cache."""

    def __init__(self) -> None:
        self._entries: dict[str, ExecutableAgent[Any]] = {}
        self._lock = Lock()

    async def get_or_build(
        self,
        cache_key: str,
        build: Callable[[], Awaitable[ExecutableAgent[Any]]],
    ) -> ExecutableAgent[Any]:
        async with self._lock:
            known = self._entries.get(cache_key)
            if known is not None:
                return known
            executable = await build()
            self._entries[cache_key] = executable
            return executable

    async def clear(self) -> None:
        """Release references to all process-local build outputs."""

        async with self._lock:
            self._entries.clear()


type SkillPackageLoader = Callable[[ResolvedSkill], Awaitable[JsonValue]]


class AgentReconstructor:
    """Map one authority-neutral snapshot through public Harness build contracts."""

    def __init__(
        self,
        skill_packages: SkillPackageLoader,
        cache: ExecutableCache,
        *,
        inline_subagents: SubagentOperator | None = None,
        async_subagents: SubagentOperator | None = None,
        shell_operator: ShellOperator | None = None,
    ) -> None:
        self._skill_packages = skill_packages
        self._cache = cache
        self._inline_subagents = inline_subagents or SubagentOperator()
        self._async_subagents = async_subagents or SubagentOperator()
        self._shell_operator = shell_operator

    async def executable(
        self,
        snapshot: ResolvedAgentSnapshot,
        environment: ResolvedEnvironmentSnapshot | None = None,
    ) -> ExecutableAgent[Any]:
        requires_environment = any(node.skills for node in snapshot.resolved_agents)
        if environment is None and requires_environment:
            raise CompositionError(
                "An Agent with managed Skills requires a compatible Environment snapshot.",
                code="agent_environment_required",
            )
        selected_environment = environment if requires_environment else None
        cache_key = snapshot.logical_agent_digest
        if selected_environment is not None:
            cache_key = f"{cache_key}:{selected_environment.logical_environment_digest}"

        async def build() -> ExecutableAgent[Any]:
            return await self._build(snapshot, selected_environment)

        return await self._cache.get_or_build(cache_key, build)

    async def _build(
        self,
        snapshot: ResolvedAgentSnapshot,
        environment: ResolvedEnvironmentSnapshot | None,
    ) -> ExecutableAgent[Any]:
        if snapshot.harness_release != _package_version("a13n-harness"):
            raise CompositionError(
                "The Agent snapshot selects another Harness release.",
                code="harness_release_mismatch",
            )
        for lock in snapshot.adapter_locks:
            if lock.dependency_kind != "model_adapter":
                continue
            registration = model_adapter_registration(lock.key)
            if registration is None or (
                lock.distribution_name != registration.distribution_name
                or lock.distribution_version != registration.distribution_version
            ):
                raise CompositionError(
                    "A locked Agent UI Model adapter changed after snapshot publication.",
                    code="model_adapter_lock_mismatch",
                    details={"model_adapter_key": lock.key},
                )
        plugin_keys = tuple(lock.key for lock in snapshot.adapter_locks if lock.dependency_kind == "harness_plugin")
        try:
            plugin_catalog = build_harness_plugin_factory_catalog(plugin_keys=plugin_keys)
        except Exception as exc:
            raise CompositionError(
                "The locked Harness plugin catalog could not be reconstructed.",
                code="plugin_factory_unavailable",
            ) from exc
        registrations = {item.plugin_key: item for item in plugin_catalog.registrations}
        for lock in snapshot.adapter_locks:
            if lock.dependency_kind != "harness_plugin":
                continue
            registration = registrations.get(lock.key)
            if registration is None or not _registration_matches(lock, registration):
                raise CompositionError(
                    "A locked Harness plugin factory changed after snapshot publication.",
                    code="plugin_factory_lock_mismatch",
                    details={"plugin_key": lock.key},
                )

        node_by_ref = {node.agent_revision: node for node in snapshot.resolved_agents}
        definitions: dict[tuple[object, bool], AgentDefinition[Any]] = {}

        async def reconstruct(node: ResolvedAgentNode, *, root: bool) -> AgentDefinition[Any]:
            definition_key = (node.agent_revision, root)
            known = definitions.get(definition_key)
            if known is not None:
                return known
            children: list[SubagentDefinition] = []
            for edge in node.subagents:
                child = await reconstruct(node_by_ref[edge.target_agent], root=False)
                children.append(
                    SubagentDefinition(
                        name=edge.name,
                        description=edge.description,
                        agent=child,
                        context=DelegationContextPolicy(
                            include_task=edge.context.include_task,
                            history=edge.context.history,
                            task_state=edge.context.task_state,
                        ),
                        identity=SubagentIdentityPolicy(
                            inherit_agent_id=edge.identity.inherit_agent_id,
                        ),
                        usage_limits=(
                            UsageLimits(**edge.usage_limits.model_dump()) if edge.usage_limits is not None else None
                        ),
                    )
                )
            plugins = tuple(
                plugin_catalog.create_plugin(
                    HarnessPluginFactoryContext(
                        plugin_key=plugin.definition.plugin_key,
                        plugin_id=plugin.definition.plugin_id,
                        configuration=plugin.definition.configuration,
                        extensions={},
                    )
                )
                for plugin in node.plugins
                if plugin.definition.enabled
            )
            capabilities = list(
                _capabilities(
                    node,
                    shell_operator=self._shell_operator if root else None,
                )
            )
            if children:
                async_execution = root and node.async_subagents.tools == "standard"
                capabilities.append(
                    SubagentCapability(
                        execution="async" if async_execution else "inline",
                        operator=self._async_subagents if async_execution else self._inline_subagents,
                    )
                )
            if node.skills:
                assert environment is not None
                capabilities.append(await self._skills(snapshot, environment, node))
            system_prompt: list[str] = []
            for block in node.prompt.definition.system_prompt_blocks:
                if block.content is None:
                    raise CompositionError(
                        "A resolved Prompt retained an unresolved source reference.",
                        code="prompt_snapshot_invalid",
                        details={"agent_id": node.agent_id},
                    )
                system_prompt.append(block.content)
            spec = AgentSpec(
                model=node.model.definition.model_id,
                name=node.agent_id,
                description=node.description,
                system_prompt=system_prompt,
                model_settings=_harness_model_settings(node) or None,
                metadata={
                    "agent_snapshot": snapshot.logical_agent_digest,
                    "agent_revision": node.agent_revision.content_digest,
                },
            )
            recovery = node.model_recovery
            definition = AgentDefinition(
                agent=spec,
                output_type=str,
                definition_id=f"agent-{node.agent_revision.content_digest[:16]}",
                model=None,
                capabilities=tuple(capabilities),
                plugins=plugins,
                subagents=tuple(children),
                model_recovery=ModelRecoveryPolicy(
                    enabled=recovery.enabled,
                    max_attempts=recovery.max_attempts,
                    continuation_prompt=recovery.continuation_prompt,
                    backoff_initial_seconds=recovery.backoff_initial_seconds,
                    backoff_max_seconds=recovery.backoff_max_seconds,
                ),
            )
            definitions[definition_key] = definition
            return definition

        root = await reconstruct(node_by_ref[snapshot.root_agent], root=True)
        try:
            return HarnessBuilder(configured_plugins_enabled=False).build(root)
        except Exception as exc:
            raise CompositionError(
                "The resolved Agent snapshot could not be built by the Harness.",
                code="agent_snapshot_build_failed",
                details={"agent_id": snapshot.root_agent.resource_id},
            ) from exc

    async def _skills(
        self,
        snapshot: ResolvedAgentSnapshot,
        environment: ResolvedEnvironmentSnapshot,
        node: ResolvedAgentNode,
    ) -> SkillsCapability:
        packages: dict[str, tuple[_PackageFile, ...]] = {}
        for skill in node.skills:
            packages[skill.definition.skill_name] = _decode_package(
                await self._skill_packages(skill),
                skill,
            )
        mount_name = node.skill_materialization_mount
        assert mount_name is not None
        mount = next(
            (item for item in environment.mounts if item.mount_name == mount_name),
            None,
        )
        if mount is None:
            raise CompositionError(
                "The Skill materialization mount is absent from the Environment snapshot.",
                code="agent_environment_incompatible",
                details={"agent_id": node.agent_id, "mount_name": mount_name},
            )
        root = f"/environment/{mount.model_alias}/.a13n/skills/{snapshot.logical_agent_digest}/{node.agent_id}"
        materializer = SnapshotSkillMaterializer(
            f"materializer-{node.agent_id}",
            root,
            packages,
        )
        source = FileSkillSource(
            f"snapshot-{node.agent_id}",
            (root,),
            required=True,
            max_entries_per_root=max(1, len(packages)),
        )
        manager = SkillManager(
            (source,),
            materializers=(materializer,),
            policy=SkillsPolicy(conflict="error", max_skills=max(1, len(packages))),
        )
        return SkillsCapability(manager)


def _harness_model_settings(node: ResolvedAgentNode) -> dict[str, JsonValue]:
    settings = node.model.definition.settings
    if node.model.definition.model_name != "test":
        return settings
    return {key: value for key, value in settings.items() if not key.startswith("test_")}


def _capabilities(
    node: ResolvedAgentNode,
    *,
    shell_operator: ShellOperator | None,
) -> tuple[AbstractCapability[Any], ...]:
    values: list[AbstractCapability[Any]] = []
    dynamic_selected = False
    for selection in node.capabilities:
        if selection.key == "a13n.dynamic-environment":
            dynamic_selected = True
            if node.environment_tools:
                values.append(
                    DynamicEnvironmentCapability(
                        DynamicEnvironmentConfiguration(),
                        operator=shell_operator,
                    )
                )
        elif selection.key == "a13n.working-state":
            values.append(WorkingStateCapability(WorkingStateConfiguration(**selection.configuration.model_dump())))
        elif selection.key == "a13n.user-interaction":
            values.append(UserInteractionCapability())
        elif selection.key == "a13n.documents":
            values.append(DocumentsCapability())
        elif selection.key == "a13n.media":
            values.append(MediaCapability())
        elif selection.key == "a13n.web":
            values.append(WebCapability())
        elif selection.key == "a13n.mcp":
            native, local = {
                "auto": (True, None),
                "local": (False, True),
                "native": (True, False),
            }[selection.execution]
            values.append(
                ContextualMCP(
                    selection.url,
                    id=selection.id,
                    headers_factory=MCPContextHeaders(
                        MCPContextHeadersConfig(
                            headers={
                                name: MCPContextHeaderBinding(
                                    source=binding.source,
                                    required=binding.required,
                                )
                                for name, binding in selection.context_headers.items()
                            }
                        )
                    ),
                    native=native,
                    local=local,
                    allowed_tools=list(selection.allowed_tools) if selection.allowed_tools is not None else None,
                    description=selection.description,
                    defer_loading=selection.defer_loading,
                )
            )
        else:
            raise CompositionError(
                "An Agent snapshot selects an unsupported Capability.",
                code="capability_schema_unavailable",
            )
    if node.environment_tools and not dynamic_selected:
        values.insert(
            0,
            DynamicEnvironmentCapability(
                DynamicEnvironmentConfiguration(),
                operator=shell_operator,
            ),
        )
    return tuple(values)


def _decode_package(payload: JsonValue, skill: ResolvedSkill) -> tuple[_PackageFile, ...]:
    if not isinstance(payload, dict):
        raise CompositionError("A managed Skill package is invalid.", code="skill_package_invalid")
    if payload.get("package_digest") != skill.definition.resolved_package_digest:
        raise CompositionError(
            "A managed Skill package does not match its pinned digest.",
            code="skill_package_mismatch",
        )
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise CompositionError("A managed Skill package is invalid.", code="skill_package_invalid")
    files: list[_PackageFile] = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise CompositionError("A managed Skill package is invalid.", code="skill_package_invalid")
        path = raw.get("path")
        encoded = raw.get("content_base64")
        expected = raw.get("sha256")
        if not isinstance(path, str) or not isinstance(encoded, str) or not isinstance(expected, str):
            raise CompositionError("A managed Skill package is invalid.", code="skill_package_invalid")
        try:
            content = base64.b64decode(encoded, validate=True)
        except ValueError as exc:
            raise CompositionError("A managed Skill package is invalid.", code="skill_package_invalid") from exc
        if hashlib.sha256(content).hexdigest() != expected:
            raise CompositionError("A managed Skill file digest is invalid.", code="skill_package_mismatch")
        files.append(_PackageFile(path=path, content=content, sha256=expected))
    return tuple(files)


def _package_version(distribution: str) -> str:
    try:
        return version(distribution)
    except PackageNotFoundError as exc:
        raise CompositionError(
            "The required runtime distribution is unavailable.",
            code="composition_dependency_missing",
            details={"distribution": distribution},
        ) from exc


def _registration_matches(
    lock: DependencyLock,
    registration: HarnessPluginFactoryRegistration,
) -> bool:
    return (
        lock.distribution_name == registration.distribution_name
        and lock.distribution_version == registration.distribution_version
    )


__all__ = [
    "AgentReconstructor",
    "ExecutableCache",
    "SkillPackageLoader",
    "SnapshotSkillMaterializer",
]
