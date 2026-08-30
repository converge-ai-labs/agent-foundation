"""Environment-aware local Skill discovery and exact package import."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import stat
import tempfile
from contextlib import AsyncExitStack
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from uuid import uuid4

import yaml
from a13n_environment_provider import (
    DirectLocalProviderRuntime,
    EnvironmentManagementAction,
    EnvironmentOperationContext,
    EnvironmentProviderError,
    EnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
)
from a13n_harness import (
    AgentIdentityRef,
    AgentInstanceContext,
    BoundSkillCatalog,
    BoundSkillCatalogItem,
    DefinitionError,
    EnvironmentAction,
    EnvironmentError,
    EnvironmentPermissionSet,
    FileOperator,
    FileSkillSource,
    SkillManager,
    SkillsPolicy,
)
from a13n_harness.environment.advanced import (
    BoundEnvironment,
    EnvironmentRuntimeMount,
    create_environment_provider_binding,
    create_environment_runtime,
)
from anyio import CancelScope, Lock, to_thread
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from a13n_ui.errors import SkillManagementError

from .catalog import CatalogRepository
from .models import (
    ConfigurationGeneration,
    ConfigurationSettings,
    LocalDirectorySettings,
    LocalSkillDiscoverySettings,
    LocalSkillSourceDefinition,
    ResolvedSkillRevisionContent,
    ResourceKind,
    SkillDefinition,
    SkillImportProvenance,
    SkillPackageSource,
    SourceTransactionEntry,
    SourceTransactionManifest,
    canonical_digest,
)

_READ_PERMISSIONS = EnvironmentPermissionSet(
    operations=frozenset(
        {
            EnvironmentAction.FILE_STAT,
            EnvironmentAction.FILE_READ_TEXT,
            EnvironmentAction.FILE_READ_BYTES,
            EnvironmentAction.FILE_LIST,
            EnvironmentAction.FILE_QUERY,
            EnvironmentAction.FILE_SEARCH_TEXT,
            EnvironmentAction.FILE_COPY_SOURCE,
        }
    )
)


class SkillPreviewItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=256)
    description: str = Field(min_length=1, max_length=16 * 1024)
    source_id: str = Field(min_length=1, max_length=256)


class SkillSourceStatus(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    source: LocalSkillSourceDefinition
    selected: bool
    selected_ordinal: int | None = Field(default=None, ge=0)
    directory_status: Literal["available", "missing", "invalid", "denied", "unavailable", "unauthorized"]


class SkillScanPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    scan_id: str = Field(min_length=1, max_length=64)
    generation_id: str = Field(min_length=1, max_length=64)
    catalog_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    items: tuple[SkillPreviewItem, ...]


class SkillConflict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    skill_name: str = Field(min_length=1, max_length=256)
    source_ids: tuple[str, ...] = Field(min_length=2, max_length=1024)
    selected_source_id: str | None = Field(default=None, min_length=1, max_length=256)


class SkillConflictPreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    generation_id: str = Field(min_length=1, max_length=64)
    catalog_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    conflicts: tuple[SkillConflict, ...]


class SkillPackageFileChange(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    path: str = Field(min_length=1, max_length=1024)
    change: Literal["added", "modified", "removed"]
    previous_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    next_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class SkillChangePreview(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    change_id: str = Field(min_length=1, max_length=64)
    generation_id: str = Field(min_length=1, max_length=64)
    catalog_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation: Literal["import", "refresh"]
    skill_id: str = Field(min_length=1, max_length=256)
    skill_name: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=512)
    description: str = Field(min_length=1, max_length=16 * 1024)
    source_id: str = Field(min_length=1, max_length=256)
    target_root_id: str = Field(min_length=1, max_length=256)
    definition_changed: bool
    files: tuple[SkillPackageFileChange, ...]


@dataclass(slots=True)
class _SkillScan:
    stack: AsyncExitStack
    generation_id: str
    catalog_digest: str
    environment: BoundEnvironment
    catalog: BoundSkillCatalog
    settings: ConfigurationSettings


@dataclass(frozen=True, slots=True)
class _ExistingSkill:
    definition: ResolvedSkillRevisionContent
    file_digests: dict[str, str]


@dataclass(slots=True)
class _PreparedSkillChange:
    preview: SkillChangePreview
    manifest: SourceTransactionManifest
    replacements: dict[str, bytes]


class SkillService:
    """Own process-local Skill previews and publish managed package source edits."""

    def __init__(
        self,
        settings: ConfigurationSettings,
        repository: CatalogRepository,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._scans: dict[str, _SkillScan] = {}
        self._prepared_changes: dict[str, _PreparedSkillChange] = {}
        self._lock = Lock()

    def update_settings(self, settings: ConfigurationSettings) -> None:
        self._settings = settings

    async def source_statuses(
        self,
        generation: ConfigurationGeneration,
        settings: ConfigurationSettings,
    ) -> tuple[SkillSourceStatus, ...]:
        selected_ordinals = {
            reference.resource_id: ordinal for ordinal, reference in enumerate(settings.skill_discovery.ordered_sources)
        }
        directories = {item.directory_id: item.path for item in settings.local_directories}
        statuses: list[SkillSourceStatus] = []
        for reference in generation.resources:
            if reference.kind is not ResourceKind.skill_source:
                continue
            revision = await self._repository.resource(reference)
            try:
                source = LocalSkillSourceDefinition.model_validate(revision.normalized_content, strict=True)
            except ValueError as exc:
                raise SkillManagementError(
                    "A selected Skill source revision is incompatible.",
                    code="skill_source_invalid",
                ) from exc
            path = directories.get(source.directory_id)
            status: Literal["available", "missing", "invalid", "denied", "unavailable", "unauthorized"]
            if path is None:
                status = "unauthorized"
            else:
                status = await to_thread.run_sync(_source_directory_status, path)
            statuses.append(
                SkillSourceStatus(
                    source=source,
                    selected=source.skill_source_id in selected_ordinals,
                    selected_ordinal=selected_ordinals.get(source.skill_source_id),
                    directory_status=status,
                )
            )
        return tuple(sorted(statuses, key=lambda item: item.source.skill_source_id))

    async def conflicts(
        self,
        discovery: LocalSkillDiscoverySettings,
        *,
        generation: ConfigurationGeneration,
        settings: ConfigurationSettings,
    ) -> SkillConflictPreview:
        sources = await self._load_sources(generation, discovery)
        sources = await _available_skill_sources(sources, settings)
        if len(sources) < 2:
            return SkillConflictPreview(
                generation_id=generation.generation_id,
                catalog_digest=generation.catalog_digest,
                conflicts=(),
            )
        stack = AsyncExitStack()
        try:
            environment, _manager, harness_sources = await _open_environment(
                stack,
                sources,
                settings,
                conflict=discovery.conflict,
                max_skills=discovery.max_skills,
            )
            by_name: dict[str, list[str]] = {}
            for source in harness_sources:
                manager = SkillManager(
                    (source,),
                    policy=SkillsPolicy(conflict="error", max_skills=discovery.max_skills),
                )
                catalog = await manager.scan_environment(environment=environment)
                for item in catalog.items:
                    by_name.setdefault(item.name, []).append(item.source_id)
            conflicts = tuple(
                SkillConflict(
                    skill_name=name,
                    source_ids=tuple(source_ids),
                    selected_source_id=(
                        None
                        if discovery.conflict == "error"
                        else source_ids[0]
                        if discovery.conflict == "prefer_earlier"
                        else source_ids[-1]
                    ),
                )
                for name, source_ids in sorted(by_name.items())
                if len(source_ids) > 1
            )
            return SkillConflictPreview(
                generation_id=generation.generation_id,
                catalog_digest=generation.catalog_digest,
                conflicts=conflicts,
            )
        except (DefinitionError, EnvironmentError, EnvironmentProviderError) as exc:
            raise _skill_boundary_error(exc, fallback_code="skill_scan_failed") from exc
        finally:
            with CancelScope(shield=True):
                await stack.aclose()

    async def scan(
        self,
        discovery: LocalSkillDiscoverySettings,
        *,
        generation: ConfigurationGeneration,
        settings: ConfigurationSettings,
    ) -> SkillScanPreview:
        sources = await self._load_sources(generation, discovery)
        sources = await _available_skill_sources(sources, settings)
        if not sources:
            return SkillScanPreview(
                scan_id=f"scan-{uuid4().hex}",
                generation_id=generation.generation_id,
                catalog_digest=generation.catalog_digest,
                items=(),
            )
        stack = AsyncExitStack()
        try:
            environment, manager, _sources = await _open_environment(
                stack,
                sources,
                settings,
                conflict=discovery.conflict,
                max_skills=discovery.max_skills,
            )
            catalog = await manager.scan_environment(environment=environment)
        except (DefinitionError, EnvironmentError, EnvironmentProviderError) as exc:
            with CancelScope(shield=True):
                await stack.aclose()
            raise _skill_boundary_error(exc, fallback_code="skill_scan_failed") from exc
        except BaseException:
            with CancelScope(shield=True):
                await stack.aclose()
            raise
        scan_id = f"scan-{uuid4().hex}"
        scan = _SkillScan(
            stack=stack,
            generation_id=generation.generation_id,
            catalog_digest=generation.catalog_digest,
            environment=environment,
            catalog=catalog,
            settings=settings,
        )
        async with self._lock:
            self._scans[scan_id] = scan
        return SkillScanPreview(
            scan_id=scan_id,
            generation_id=generation.generation_id,
            catalog_digest=generation.catalog_digest,
            items=tuple(
                SkillPreviewItem(name=item.name, description=item.description, source_id=item.source_id)
                for item in catalog.items
            ),
        )

    async def preview_import(
        self,
        *,
        scan_id: str,
        skill_name: str,
        skill_id: str,
        display_name: str,
        target_root_id: str,
    ) -> SkillChangePreview:
        return await self._prepare_change(
            scan_id=scan_id,
            skill_name=skill_name,
            skill_id=skill_id,
            display_name=display_name,
            target_root_id=target_root_id,
            existing_requirement="absent",
        )

    async def preview_refresh(
        self,
        *,
        scan_id: str,
        skill_name: str,
        skill_id: str,
    ) -> SkillChangePreview:
        return await self._prepare_change(
            scan_id=scan_id,
            skill_name=skill_name,
            skill_id=skill_id,
            display_name=None,
            target_root_id=None,
            existing_requirement="present",
        )

    async def accept_change(
        self,
        change_id: str,
        *,
        expected_operation: Literal["import", "refresh"] | None = None,
    ) -> tuple[SourceTransactionManifest, dict[str, bytes]]:
        async with self._lock:
            prepared = self._prepared_changes.get(change_id)
            if prepared is not None and (
                expected_operation is None or prepared.preview.operation == expected_operation
            ):
                self._prepared_changes.pop(change_id)
            elif prepared is not None:
                raise SkillManagementError(
                    "The prepared Skill change has a different operation type.",
                    code="skill_change_mismatch",
                )
        if prepared is None:
            raise SkillManagementError(
                "The prepared Skill change is unavailable.",
                code="skill_change_missing",
            )
        generation = await self._repository.current_generation()
        if generation is None or generation.generation_id != prepared.preview.generation_id:
            raise SkillManagementError(
                "The prepared Skill change belongs to a stale configuration generation.",
                code="skill_catalog_stale",
            )
        return prepared.manifest, dict(prepared.replacements)

    async def discard_change(self, change_id: str) -> None:
        async with self._lock:
            self._prepared_changes.pop(change_id, None)

    async def _prepare_change(
        self,
        *,
        scan_id: str,
        skill_name: str,
        skill_id: str,
        display_name: str | None,
        target_root_id: str | None,
        existing_requirement: Literal["absent", "present", "any"],
    ) -> SkillChangePreview:
        async with self._lock:
            scan = self._scans.pop(scan_id, None)
        if scan is None:
            raise SkillManagementError("The selected Skill scan is unavailable.", code="skill_scan_missing")
        try:
            generation = await self._repository.current_generation()
            if generation is None or generation.generation_id != scan.generation_id:
                raise SkillManagementError(
                    "The selected Skill scan belongs to a stale configuration generation.",
                    code="skill_catalog_stale",
                )
            selected = scan.catalog.select(frozenset({skill_name}))
            if len(selected.items) != 1:
                raise SkillManagementError("The selected Skill is absent from the scan.", code="skill_not_found")
            try:
                selected.require_current(scan.environment)
                item = selected.items[0]
                scope = scan.environment.select_files(item.path)
            except (DefinitionError, EnvironmentError, EnvironmentProviderError) as exc:
                raise _skill_boundary_error(exc, fallback_code="skill_catalog_stale") from exc
            if scope.resolved_path != item.directory or scope.observed_generation != item.observed_generation:
                raise SkillManagementError(
                    "The selected Skill route changed after discovery.",
                    code="skill_catalog_stale",
                )
            async with scan.environment.open_files(scope) as files:
                payload, copied = await _copy_package(files, item, scan.settings)
            await validate_managed_skill_package(
                copied,
                expected_name=item.name,
                expected_description=item.description,
                settings=scan.settings,
            )
            existing = await self._existing_skill(generation, skill_id=skill_id)
            if existing_requirement == "present" and existing is None:
                raise SkillManagementError(
                    "Refreshing a Skill requires an existing managed revision.",
                    code="skill_not_found",
                )
            if existing_requirement == "absent" and existing is not None:
                raise SkillManagementError(
                    "Importing a Skill requires a new managed Skill ID.",
                    code="skill_already_exists",
                )
            if existing_requirement == "present" and existing is not None:
                provenance = existing.definition.imported_from
                if provenance is None or provenance.source_id != item.source_id or provenance.skill_name != item.name:
                    raise SkillManagementError(
                        "Refreshing a Skill requires its recorded discovery source and name.",
                        code="skill_refresh_source_mismatch",
                    )
            if existing is None:
                if display_name is None or target_root_id is None:
                    raise SkillManagementError(
                        "The managed Skill target is unavailable.",
                        code="skill_not_found",
                    )
            else:
                if display_name is None:
                    display_name = existing.definition.display_name
                if target_root_id is None:
                    target_root_id = existing.definition.package.root_id
            if existing is not None and existing.definition.package.root_id != target_root_id:
                raise SkillManagementError(
                    "Refreshing a managed Skill cannot move it between definition roots.",
                    code="skill_target_conflict",
                )
            existing_paths = ()
            if existing is not None:
                existing_paths = tuple(
                    f"{existing.definition.package.relative_path}/{path}" for path in existing.file_digests
                )
            manifest, replacements, definition = self._build_import(
                generation.catalog_digest,
                item,
                payload,
                copied,
                skill_id=skill_id,
                display_name=display_name,
                target_root_id=target_root_id,
                existing_paths=existing_paths,
                settings=scan.settings,
            )
            change_id = f"skill-change-{uuid4().hex[:16]}"
            preview = SkillChangePreview(
                change_id=change_id,
                generation_id=generation.generation_id,
                catalog_digest=generation.catalog_digest,
                operation="refresh" if existing is not None else "import",
                skill_id=skill_id,
                skill_name=item.name,
                display_name=display_name,
                description=item.description,
                source_id=item.source_id,
                target_root_id=target_root_id,
                definition_changed=_definition_changed(existing, definition),
                files=_package_file_changes(existing, copied),
            )
            async with self._lock:
                self._prepared_changes[change_id] = _PreparedSkillChange(
                    preview=preview,
                    manifest=manifest,
                    replacements=replacements,
                )
            return preview
        finally:
            with CancelScope(shield=True):
                await scan.stack.aclose()

    async def discard_scan(self, scan_id: str) -> None:
        async with self._lock:
            scan = self._scans.pop(scan_id, None)
        if scan is not None:
            await scan.stack.aclose()

    async def close(self) -> None:
        async with self._lock:
            scans = tuple(self._scans.values())
            self._scans.clear()
            self._prepared_changes.clear()
        for scan in scans:
            await scan.stack.aclose()

    async def _load_sources(
        self,
        generation: ConfigurationGeneration,
        discovery: LocalSkillDiscoverySettings,
    ) -> tuple[LocalSkillSourceDefinition, ...]:
        selected_by_id = {
            reference.resource_id: reference
            for reference in generation.resources
            if reference.kind is ResourceKind.skill_source
        }
        sources: list[LocalSkillSourceDefinition] = []
        for source_ref in discovery.ordered_sources:
            reference = selected_by_id.get(source_ref.resource_id)
            if reference is None:
                raise SkillManagementError(
                    "A selected Skill source is absent from the captured generation.",
                    code="skill_source_missing",
                )
            revision = await self._repository.resource(reference)
            try:
                sources.append(LocalSkillSourceDefinition.model_validate(revision.normalized_content, strict=True))
            except ValueError as exc:
                raise SkillManagementError(
                    "A selected Skill source revision is incompatible.",
                    code="skill_source_invalid",
                ) from exc
        return tuple(sources)

    async def _existing_skill(
        self,
        generation: ConfigurationGeneration,
        *,
        skill_id: str,
    ) -> _ExistingSkill | None:
        reference = next(
            (item for item in generation.resources if item.kind is ResourceKind.skill and item.resource_id == skill_id),
            None,
        )
        if reference is None:
            return None
        revision = await self._repository.resource(reference)
        try:
            definition = ResolvedSkillRevisionContent.model_validate(revision.normalized_content, strict=True)
        except ValueError as exc:
            raise SkillManagementError(
                "The existing managed Skill revision is incompatible.",
                code="skill_package_invalid",
            ) from exc
        payload = await self._repository.skill_package(reference)
        files_value = payload.get("files") if isinstance(payload, dict) else None
        if not isinstance(files_value, list):
            raise SkillManagementError(
                "The existing managed Skill package is invalid.",
                code="skill_package_invalid",
            )
        file_digests: dict[str, str] = {}
        for item in files_value:
            if not isinstance(item, dict):
                raise SkillManagementError(
                    "The existing managed Skill package manifest is invalid.",
                    code="skill_package_invalid",
                )
            path = item.get("path")
            digest = item.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                raise SkillManagementError(
                    "The existing managed Skill package manifest is invalid.",
                    code="skill_package_invalid",
                )
            if path in file_digests:
                raise SkillManagementError(
                    "The existing managed Skill package manifest is invalid.",
                    code="skill_package_invalid",
                )
            file_digests[path] = digest
        return _ExistingSkill(definition=definition, file_digests=file_digests)

    def _build_import(
        self,
        base_catalog_digest: str,
        item: BoundSkillCatalogItem,
        payload: JsonValue,
        files: tuple[tuple[str, bytes], ...],
        *,
        skill_id: str,
        display_name: str,
        target_root_id: str,
        existing_paths: tuple[str, ...],
        settings: ConfigurationSettings,
    ) -> tuple[SourceTransactionManifest, dict[str, bytes], SkillDefinition]:
        roots = {root.root_id: root for root in settings.ordered_roots}
        target = roots.get(target_root_id)
        if target is None or not target.writable:
            raise SkillManagementError("The managed Skill target root is not writable.", code="skill_target_denied")
        package_relative = f"managed-skills/{skill_id}"
        definition = SkillDefinition(
            schema_version="1",
            skill_id=skill_id,
            display_name=display_name,
            skill_name=item.name,
            description=item.description,
            package=SkillPackageSource(root_id=target_root_id, relative_path=package_relative),
            imported_from=SkillImportProvenance(
                source_id=item.source_id,
                skill_name=item.name,
                source_catalog_digest=canonical_digest(
                    {
                        "name": item.name,
                        "description": item.description,
                        "package_digest": cast(dict[str, JsonValue], payload)["package_digest"],
                    }
                ),
            ),
        )
        replacements: dict[str, bytes] = {
            f"skills/{skill_id}.yaml": yaml.safe_dump(
                definition.model_dump(mode="json"),
                allow_unicode=True,
                sort_keys=True,
            ).encode("utf-8")
        }
        for path, content in files:
            replacements[f"{package_relative}/{path}"] = content
        entries = tuple(
            sorted(
                (
                    *(
                        SourceTransactionEntry(
                            relative_path=path,
                            operation="replace",
                            content_digest=hashlib.sha256(content).hexdigest(),
                        )
                        for path, content in replacements.items()
                    ),
                    *(
                        SourceTransactionEntry(relative_path=path, operation="delete")
                        for path in existing_paths
                        if path not in replacements
                    ),
                ),
                key=lambda entry: entry.relative_path,
            )
        )
        manifest = SourceTransactionManifest(
            schema_version="1",
            transaction_id=f"transaction-{uuid4().hex[:16]}",
            root_id=target_root_id,
            base_catalog_digest=base_catalog_digest,
            entries=entries,
        )
        return manifest, replacements, definition


def _definition_changed(existing: _ExistingSkill | None, definition: SkillDefinition) -> bool:
    if existing is None:
        return True
    current = existing.definition.model_dump(
        mode="json",
        exclude={"resolved_package_digest"},
    )
    return current != definition.model_dump(mode="json")


def _package_file_changes(
    existing: _ExistingSkill | None,
    files: tuple[tuple[str, bytes], ...],
) -> tuple[SkillPackageFileChange, ...]:
    previous = existing.file_digests if existing is not None else {}
    current = {path: hashlib.sha256(content).hexdigest() for path, content in files}
    changes: list[SkillPackageFileChange] = []
    for path in sorted(set(previous) | set(current)):
        old_digest = previous.get(path)
        new_digest = current.get(path)
        if old_digest == new_digest:
            continue
        if old_digest is None:
            change: Literal["added", "modified", "removed"] = "added"
        elif new_digest is None:
            change = "removed"
        else:
            change = "modified"
        changes.append(
            SkillPackageFileChange(
                path=path,
                change=change,
                previous_sha256=old_digest,
                next_sha256=new_digest,
            )
        )
    return tuple(changes)


async def _available_skill_sources(
    sources: tuple[LocalSkillSourceDefinition, ...],
    settings: ConfigurationSettings,
) -> tuple[LocalSkillSourceDefinition, ...]:
    directories = {item.directory_id: item.path for item in settings.local_directories}
    available: list[LocalSkillSourceDefinition] = []
    for source in sources:
        path = directories.get(source.directory_id)
        if path is None:
            raise SkillManagementError(
                "A Skill source directory alias is unavailable.",
                code="skill_source_unauthorized",
            )
        if await to_thread.run_sync(_source_directory_exists, path):
            available.append(source)
        elif source.required:
            raise SkillManagementError(
                "A required Skill source directory is unavailable.",
                code="skill_source_unavailable",
            )
    return tuple(available)


async def _open_environment(
    stack: AsyncExitStack,
    sources: tuple[LocalSkillSourceDefinition, ...],
    settings: ConfigurationSettings,
    *,
    conflict: Literal["error", "prefer_earlier", "prefer_later"],
    max_skills: int,
) -> tuple[BoundEnvironment, SkillManager, tuple[FileSkillSource, ...]]:
    directories = {item.directory_id: item.path for item in settings.local_directories}
    factory_catalog = build_environment_provider_factory_catalog(builtin_keys=("a13n.direct-local",))
    mounts: dict[str, EnvironmentRuntimeMount] = {}
    harness_sources: list[FileSkillSource] = []
    alias_by_directory: dict[str, str] = {}
    for source in sources:
        path = directories.get(source.directory_id)
        if path is None:
            raise SkillManagementError(
                "A Skill source directory alias is unavailable.", code="skill_source_unauthorized"
            )
        alias = alias_by_directory.get(source.directory_id)
        if alias is None:
            alias = f"source-{len(alias_by_directory) + 1}"
            alias_by_directory[source.directory_id] = alias
            spec = EnvironmentProviderSpec(
                provider_key="a13n.direct-local",
                schema_version="1",
                parameters={
                    "environment_id": f"skill-source-{len(alias_by_directory)}",
                    "root": {"path": str(path), "read_only": True},
                },
            )
            manager = factory_catalog.create_provider(spec, runtime=DirectLocalProviderRuntime())
            managed = await manager.create(
                operation=EnvironmentOperationContext(
                    operation_id=f"skill-create-{len(alias_by_directory)}",
                    action=EnvironmentManagementAction.CREATE,
                    resource_correlation=f"skill-source-{len(alias_by_directory)}",
                    attempt=1,
                )
            )
            entered = await stack.enter_async_context(managed)
            attachment = await stack.enter_async_context(entered.acquire_attachment())
            provider_binding = create_environment_provider_binding(attachment)
            mounts[alias] = EnvironmentRuntimeMount(
                binding=provider_binding,
                permission_ceiling=_READ_PERMISSIONS,
                working_directory="/",
            )
        roots = tuple(f"/environment/{alias}{root if root != '/' else ''}" for root in source.roots)
        harness_sources.append(
            FileSkillSource(
                source.skill_source_id,
                roots,
                required=source.required,
                max_entries_per_root=source.max_entries_per_root,
            )
        )
    runtime = create_environment_runtime(
        mounts=mounts,
    )
    environment = await stack.enter_async_context(
        runtime.bind(
            run_id=f"skill-scan-{uuid4().hex}",
            instance=AgentInstanceContext(
                identity=AgentIdentityRef(issuer="a13n-ui", subject="skill-management"),
                agent_instance_id=f"skill-manager-{uuid4().hex}",
            ),
        )
    )
    manager = SkillManager(
        harness_sources,
        policy=SkillsPolicy(conflict=conflict, max_skills=max_skills),
    )
    return environment, manager, tuple(harness_sources)


async def _copy_package(
    files: FileOperator,
    item: BoundSkillCatalogItem,
    settings: ConfigurationSettings,
) -> tuple[JsonValue, tuple[tuple[str, bytes], ...]]:
    previous: tuple[tuple[str, bytes], ...] | None = None
    for _attempt in range(settings.stable_read_attempts + 1):
        payload, copied = await _copy_package_once(files, item, settings)
        if copied == previous:
            return payload, copied
        previous = copied
    raise SkillManagementError(
        "A Skill package changed during bounded stable reads.",
        code="skill_package_unstable",
    )


async def _copy_package_once(
    files: FileOperator,
    item: BoundSkillCatalogItem,
    settings: ConfigurationSettings,
) -> tuple[JsonValue, tuple[tuple[str, bytes], ...]]:
    pending = [item.path]
    copied: list[tuple[str, bytes]] = []
    entry_count = 0
    total = 0
    prefix = item.path.rstrip("/") + "/"
    while pending:
        directory = pending.pop()
        result = await files.list(
            directory,
            offset=0,
            max_results=settings.max_skill_package_files + 1,
            include_hidden=True,
        )
        if result.has_more:
            raise SkillManagementError("A Skill package exceeds its entry limit.", code="skill_package_limit")
        for metadata in sorted(result.entries, key=lambda entry: entry.path):
            relative = metadata.path.removeprefix(prefix)
            path = PurePosixPath(relative)
            if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
                raise SkillManagementError("A Skill package path is invalid.", code="skill_package_invalid")
            entry_count += 1
            if entry_count > settings.max_skill_package_files or len(path.parts) > settings.max_skill_package_depth:
                raise SkillManagementError("A Skill package exceeds configured limits.", code="skill_package_limit")
            if metadata.kind == "directory":
                pending.append(metadata.path)
                continue
            if metadata.kind != "file":
                raise SkillManagementError(
                    "Skill packages may contain only regular files and directories.",
                    code="skill_package_invalid",
                )
            content = await files.read_bytes(metadata.path)
            total += len(content)
            if total > settings.max_skill_package_bytes:
                raise SkillManagementError("A Skill package exceeds configured limits.", code="skill_package_limit")
            copied.append((path.as_posix(), bytes(content)))
    copied.sort(key=lambda entry: entry[0])
    if not any(path == "SKILL.md" for path, _content in copied):
        raise SkillManagementError("A Skill package must contain SKILL.md.", code="skill_package_invalid")
    manifest: list[JsonValue] = []
    payload_files: list[JsonValue] = []
    for path, content in copied:
        digest = hashlib.sha256(content).hexdigest()
        manifest.append({"path": path, "sha256": digest})
        payload_files.append(
            {
                "path": path,
                "sha256": digest,
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    payload = cast(
        JsonValue,
        {
            "schema_version": "1",
            "package_digest": canonical_digest(manifest),
            "files": payload_files,
        },
    )
    return payload, tuple(copied)


async def validate_managed_skill_package(
    copied: tuple[tuple[str, bytes], ...],
    *,
    expected_name: str,
    expected_description: str,
    settings: ConfigurationSettings,
) -> None:
    root = await to_thread.run_sync(partial(tempfile.mkdtemp, prefix="a13n-skill-copy-"))
    try:
        await to_thread.run_sync(partial(_write_package, root, copied))
        source = LocalSkillSourceDefinition(
            schema_version="1",
            skill_source_id="skill-source-copy",
            display_name="Copied Skill",
            directory_id="directory-copy",
            roots=("/",),
            required=True,
            max_entries_per_root=settings.max_skill_package_files,
        )
        copied_settings = settings.model_copy(
            update={"local_directories": (LocalDirectorySettings(directory_id="directory-copy", path=Path(root)),)}
        )
        stack = AsyncExitStack()
        try:
            try:
                environment, manager, _sources = await _open_environment(
                    stack,
                    (source,),
                    copied_settings,
                    conflict="error",
                    max_skills=1,
                )
                catalog = await manager.scan_environment(environment=environment)
            except (DefinitionError, EnvironmentError, EnvironmentProviderError) as exc:
                raise _skill_boundary_error(exc, fallback_code="skill_package_invalid") from exc
            if (
                len(catalog.items) != 1
                or catalog.items[0].name != expected_name
                or catalog.items[0].description != expected_description
            ):
                raise SkillManagementError(
                    "The copied Skill package does not reproduce its scanned metadata.",
                    code="skill_package_invalid",
                )
        finally:
            await stack.aclose()
    finally:
        await to_thread.run_sync(partial(shutil.rmtree, root, ignore_errors=True))


def _source_directory_status(
    path: Path,
) -> Literal["available", "missing", "invalid", "denied", "unavailable"]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return "missing"
    except PermissionError:
        return "denied"
    except OSError:
        return "unavailable"
    return "available" if stat.S_ISDIR(metadata.st_mode) and not path.is_symlink() else "invalid"


def _source_directory_exists(path: Path) -> bool:
    try:
        path.stat(follow_symlinks=False)
    except FileNotFoundError:
        return False
    except PermissionError as exc:
        raise SkillManagementError(
            "A Skill source directory is not accessible.",
            code="skill_source_denied",
        ) from exc
    except OSError as exc:
        raise SkillManagementError(
            "A Skill source directory is unavailable.",
            code="skill_source_unavailable",
        ) from exc
    return True


def _skill_boundary_error(error: Exception, *, fallback_code: str) -> SkillManagementError:
    details: dict[str, str] = {}
    if isinstance(error, (DefinitionError, EnvironmentError, EnvironmentProviderError)):
        code = error.code
        for key in ("skill", "other_skill", "source_id"):
            value = error.details.get(key)
            if isinstance(value, str):
                details[key] = value[:256]
    else:
        code = fallback_code
    if not code:
        code = fallback_code
    return SkillManagementError(
        "The Environment-aware Skill operation could not complete safely.",
        code=code[:64],
        details=details,
    )


def _write_package(root: str, copied: tuple[tuple[str, bytes], ...]) -> None:
    root_path = Path(root)
    for relative, content in copied:
        destination = root_path.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)


__all__ = [
    "SkillChangePreview",
    "SkillConflict",
    "SkillConflictPreview",
    "SkillPackageFileChange",
    "SkillPreviewItem",
    "SkillScanPreview",
    "SkillService",
    "SkillSourceStatus",
    "validate_managed_skill_package",
]
