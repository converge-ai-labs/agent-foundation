"""Stable strict source loading and complete resource graph validation."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml
from a13n_environment_provider import (
    EnvironmentProviderSpec,
    build_environment_provider_factory_catalog,
    discover_environment_provider_factory_references,
)
from a13n_harness import (
    build_harness_plugin_factory_catalog,
    discover_harness_plugin_factory_references,
)
from anyio import to_thread
from pydantic import JsonValue, ValidationError

from a13n_ui.errors import ConfigurationError

from .models import (
    AgentDefinitionDocument,
    ConfigurationSettings,
    DependencyLock,
    EnvironmentDefinitionDocument,
    LocalSkillSourceDefinition,
    ModelDefinition,
    PluginInstanceDefinition,
    PromptBlock,
    PromptDefinition,
    ResolvedSkillRevisionContent,
    ResourceDocument,
    ResourceKind,
    ResourceRef,
    ResourceRevision,
    ResourceRevisionRef,
    SafeSourceRef,
    SkillDefinition,
    SourceTransactionManifest,
    canonical_digest,
    resource_identity,
    restart_settings_digest,
)
from .transactions import read_source_overlay

_NAMESPACE_MODELS: dict[str, type[ResourceDocument]] = {
    "models": ModelDefinition,
    "prompts": PromptDefinition,
    "plugins": PluginInstanceDefinition,
    "skill-sources": LocalSkillSourceDefinition,
    "skills": SkillDefinition,
    "agents": AgentDefinitionDocument,
    "environments": EnvironmentDefinitionDocument,
}
_SOURCE_SUFFIXES = frozenset({".yaml", ".yml", ".json"})


@dataclass(frozen=True, slots=True)
class SkillPackageCandidate:
    revision: ResourceRevisionRef
    payload: JsonValue


@dataclass(frozen=True, slots=True)
class CatalogCandidate:
    settings: ConfigurationSettings
    settings_digest: str
    restart_settings_digest: str
    catalog_digest: str
    revisions: tuple[ResourceRevision, ...]
    skill_packages: tuple[SkillPackageCandidate, ...]
    availability: tuple[DependencyLock, ...]
    source_transactions: tuple[SourceTransactionManifest, ...]


@dataclass(frozen=True, slots=True)
class _LoadedDocument:
    layer: int
    root_id: str
    root: Path
    path: Path
    relative_path: str
    document: ResourceDocument
    overlay: Mapping[str, bytes | None]


async def load_configuration_settings(
    path: Path | None,
    fallback: ConfigurationSettings,
) -> ConfigurationSettings:
    """Load one strict file-backed process configuration or use the bootstrap value."""

    if path is None:
        return fallback
    if path.suffix.lower() not in _SOURCE_SUFFIXES:
        raise _error("process_settings_invalid", "Process settings must use YAML or JSON.")
    content = await _stable_read(path, fallback)
    raw = _parse_document(path, content, fallback)
    try:
        serialized = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return ConfigurationSettings.model_validate_json(serialized, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        raise _error(
            "process_settings_invalid",
            "The process settings document is invalid.",
            details={"validation_error_count": exc.error_count() if isinstance(exc, ValidationError) else 1},
        ) from exc


async def load_catalog_candidate(
    settings: ConfigurationSettings,
    *,
    source_overlays: Mapping[str, Mapping[str, bytes | None]] | None = None,
) -> CatalogCandidate:
    """Read every selected source at a stable point and validate one complete graph."""

    loaded: list[_LoadedDocument] = []
    overlays: dict[str, Mapping[str, bytes | None]] = {}
    selected_overlays = source_overlays or {}
    source_transactions: list[SourceTransactionManifest] = []
    source_count = 0
    for layer, root in enumerate(settings.ordered_roots):
        if root.root_id in selected_overlays:
            active_manifest = None
            overlay = selected_overlays[root.root_id]
        else:
            active_manifest, overlay = await to_thread.run_sync(read_source_overlay, root.path)
        if active_manifest is not None:
            if active_manifest.root_id != root.root_id:
                raise _error(
                    "source_transaction_invalid",
                    "The active source transaction selects the wrong definition root.",
                )
            source_transactions.append(active_manifest)
        overlays[root.root_id] = overlay
        documents = await to_thread.run_sync(partial(_discover_root, root.path, settings.max_source_files, overlay))
        source_count += len(documents)
        if source_count > settings.max_source_files:
            raise _error("configuration_source_limit", "Configuration contains too many source documents.")
        for path, namespace, relative_path in documents:
            selected_content = overlay.get(relative_path)
            content = selected_content if isinstance(selected_content, bytes) else await _stable_read(path, settings)
            raw = _parse_document(path, content, settings)
            model_type = _NAMESPACE_MODELS[namespace]
            try:
                document = model_type.model_validate(raw, strict=True)
            except ValidationError as exc:
                raise _error(
                    "configuration_document_invalid",
                    "A configuration resource document is invalid.",
                    details={"source_id": root.root_id, "validation_error_count": exc.error_count()},
                ) from exc
            loaded.append(
                _LoadedDocument(
                    layer=layer,
                    root_id=root.root_id,
                    root=root.path,
                    path=path,
                    relative_path=relative_path,
                    document=document,
                    overlay=overlay,
                )
            )

    availability, plugin_locks, provider_locks = _availability(settings)
    selected = _select_precedence(loaded)
    local_directory_ids = {item.directory_id for item in settings.local_directories}
    revisions: list[ResourceRevision] = []
    packages: list[SkillPackageCandidate] = []
    identities = {
        (identity.kind.value, identity.resource_id)
        for item in selected
        for identity in (resource_identity(item.document),)
    }
    for reference in settings.skill_discovery.ordered_sources:
        if (reference.kind.value, reference.resource_id) not in identities:
            raise _error(
                "configuration_reference_missing",
                "Skill discovery selects a missing source resource.",
            )

    for item in selected:
        document = await _normalize_document(item, settings)
        identity = resource_identity(document)
        locks: list[DependencyLock] = []
        if isinstance(document, ModelDefinition):
            if document.provider_key not in settings.model_adapter_keys:
                raise _error("model_adapter_unavailable", "A Model selects an unavailable adapter.")
            locks.append(DependencyLock(dependency_kind="model_adapter", key=document.provider_key))
        elif isinstance(document, PluginInstanceDefinition):
            lock = plugin_locks.get(document.plugin_key)
            if document.plugin_key not in settings.plugin_keys or lock is None:
                raise _error("plugin_factory_unavailable", "A Plugin selects an unavailable factory.")
            locks.append(lock)
        elif isinstance(document, LocalSkillSourceDefinition):
            if document.directory_id not in local_directory_ids:
                raise _error("skill_source_unauthorized", "A Skill source selects an unauthorized directory alias.")
        elif isinstance(document, EnvironmentDefinitionDocument):
            for binding in document.bindings:
                lock = provider_locks.get(binding.provider_key)
                if lock is None:
                    raise _error("provider_factory_unavailable", "An Environment selects an unavailable provider.")
                locks.append(lock)
                _validate_provider_binding(binding, settings)

        package_payload: JsonValue | None = None
        normalized_value = document.model_dump(mode="json")
        if isinstance(document, SkillDefinition):
            package_payload = await _read_skill_package(document, settings, overlays)
            if not isinstance(package_payload, dict) or not isinstance(package_payload.get("package_digest"), str):
                raise _error("skill_package_invalid", "A managed Skill package has no canonical digest.")
            normalized_value["resolved_package_digest"] = package_payload["package_digest"]
            try:
                normalized_value = ResolvedSkillRevisionContent.model_validate(
                    normalized_value, strict=True
                ).model_dump(mode="json")
            except ValidationError as exc:
                raise _error("skill_package_invalid", "A managed Skill revision is invalid.") from exc
        normalized = cast(JsonValue, normalized_value)
        content_digest = canonical_digest(
            {
                "schema_version": document.schema_version,
                "normalized_content": normalized,
                "dependencies": [lock.model_dump(mode="json") for lock in locks],
            }
        )
        revision_ref = ResourceRevisionRef(
            kind=identity.kind,
            resource_id=identity.resource_id,
            content_digest=content_digest,
        )
        revision = ResourceRevision(
            ref=revision_ref,
            schema_version=document.schema_version,
            normalized_content=normalized,
            source=SafeSourceRef(source_id=item.root_id, relative_path=item.relative_path),
            dependency_provenance=tuple(locks),
        )
        revisions.append(revision)
        if package_payload is not None:
            packages.append(SkillPackageCandidate(revision=revision_ref, payload=package_payload))

    _validate_graph(revisions, identities)
    revisions.sort(key=lambda revision: (revision.ref.kind.value, revision.ref.resource_id))
    packages.sort(key=lambda package: package.revision.resource_id)
    settings_dump = settings.model_dump(mode="json")
    settings_digest = canonical_digest(settings_dump)
    catalog_digest = canonical_digest(
        {
            "settings": settings_digest,
            "roots": [root.root_id for root in settings.ordered_roots],
            "resources": [revision.ref.model_dump(mode="json") for revision in revisions],
            "availability": [lock.model_dump(mode="json") for lock in availability],
        }
    )
    return CatalogCandidate(
        settings=settings,
        settings_digest=settings_digest,
        restart_settings_digest=restart_settings_digest(settings),
        catalog_digest=catalog_digest,
        revisions=tuple(revisions),
        skill_packages=tuple(packages),
        availability=availability,
        source_transactions=tuple(source_transactions),
    )


def _discover_root(
    root: Path,
    max_files: int,
    overlay: Mapping[str, bytes | None],
) -> tuple[tuple[Path, str, str], ...]:
    try:
        root_stat = root.stat(follow_symlinks=False)
    except FileNotFoundError:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_stat = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise _error("configuration_source_unavailable", "A definition root cannot be inspected.") from exc
    if not stat.S_ISDIR(root_stat.st_mode) or root.is_symlink():
        raise _error("configuration_source_invalid", "A definition root must be a real directory.")
    discovered: dict[str, tuple[Path, str, str]] = {}
    for namespace in _NAMESPACE_MODELS:
        namespace_path = root / namespace
        if not namespace_path.exists():
            continue
        try:
            metadata = namespace_path.stat(follow_symlinks=False)
        except OSError as exc:
            raise _error("configuration_source_unavailable", "A resource namespace cannot be inspected.") from exc
        if not stat.S_ISDIR(metadata.st_mode) or namespace_path.is_symlink():
            raise _error("configuration_source_invalid", "A resource namespace must be a real directory.")
        for current, directories, files in os.walk(namespace_path, followlinks=False):
            directories.sort()
            files.sort()
            current_path = Path(current)
            for directory in tuple(directories):
                candidate = current_path / directory
                if candidate.is_symlink():
                    raise _error("configuration_source_invalid", "Configuration source symlinks are forbidden.")
            for name in files:
                candidate = current_path / name
                if candidate.suffix.lower() not in _SOURCE_SUFFIXES:
                    continue
                try:
                    relative = candidate.relative_to(root).as_posix()
                    file_stat = candidate.stat(follow_symlinks=False)
                except (OSError, ValueError) as exc:
                    raise _error("configuration_source_invalid", "A source file escaped its definition root.") from exc
                if not stat.S_ISREG(file_stat.st_mode) or candidate.is_symlink():
                    raise _error("configuration_source_invalid", "Resource documents must be regular files.")
                discovered[relative] = (candidate, namespace, relative)
    for relative, content in overlay.items():
        path = PurePosixPath(relative)
        namespace = path.parts[0] if path.parts else ""
        if namespace not in _NAMESPACE_MODELS or path.suffix.lower() not in _SOURCE_SUFFIXES:
            continue
        if content is None:
            discovered.pop(relative, None)
        else:
            discovered[relative] = (root.joinpath(*path.parts), namespace, relative)
    if len(discovered) > max_files:
        raise _error("configuration_source_limit", "A definition root contains too many documents.")
    return tuple(discovered[key] for key in sorted(discovered))


async def _stable_read(path: Path, settings: ConfigurationSettings) -> bytes:
    for _attempt in range(settings.stable_read_attempts):
        try:
            before = await to_thread.run_sync(partial(path.stat, follow_symlinks=False))
            if not stat.S_ISREG(before.st_mode) or before.st_size > settings.max_source_bytes:
                raise _error("configuration_source_limit", "A configuration document exceeds its size limit.")
            content = await to_thread.run_sync(path.read_bytes)
            after = await to_thread.run_sync(partial(path.stat, follow_symlinks=False))
        except ConfigurationError:
            raise
        except OSError as exc:
            raise _error("configuration_source_unavailable", "A configuration document cannot be read.") from exc
        fingerprint_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        fingerprint_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if fingerprint_before == fingerprint_after and len(content) == before.st_size:
            return content
    raise _error("configuration_source_unstable", "A configuration document changed during bounded stable reads.")


def _parse_document(path: Path, content: bytes, settings: ConfigurationSettings) -> Mapping[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error("configuration_document_invalid", "A configuration document is not valid UTF-8.") from exc
    if "\x00" in text:
        raise _error("configuration_document_invalid", "A configuration document contains NUL.")
    try:
        if path.suffix.lower() == ".json":
            value = json.loads(
                text, object_pairs_hook=_reject_duplicate_json_pairs, parse_constant=_reject_json_constant
            )
        else:
            _reject_yaml_graph_features(text)
            value = yaml.load(text, Loader=_UniqueSafeLoader)
    except ConfigurationError:
        raise
    except (json.JSONDecodeError, yaml.YAMLError, UnicodeError, ValueError) as exc:
        raise _error("configuration_document_invalid", "A configuration document has invalid syntax.") from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _error("configuration_document_invalid", "A resource document must be a string-keyed mapping.")
    nodes, depth = _measure_tree(value)
    if nodes > settings.max_yaml_nodes or depth > settings.max_document_depth:
        raise _error("configuration_document_limit", "A configuration document exceeds structural limits.")
    return value


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    pairs = loader.construct_pairs(node, deep=deep)
    result: dict[Any, Any] = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise yaml.constructor.ConstructorError(None, None, "mapping keys must be strings", node.start_mark)
        if key in result:
            raise yaml.constructor.ConstructorError(None, None, "duplicate mapping key", node.start_mark)
        result[key] = value
    return result


_UniqueSafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _reject_yaml_graph_features(text: str) -> None:
    try:
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise _error("configuration_document_invalid", "YAML anchors and aliases are forbidden.")
            tag = getattr(event, "tag", None)
            if tag is not None and not str(tag).startswith("tag:yaml.org,2002:"):
                raise _error("configuration_document_invalid", "Custom YAML tags are forbidden.")
    except yaml.YAMLError as exc:
        raise _error("configuration_document_invalid", "A YAML document has invalid syntax.") from exc


def _reject_duplicate_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON mapping key")
        value[key] = item
    return value


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _measure_tree(value: Any, depth: int = 1) -> tuple[int, int]:
    if isinstance(value, dict):
        children = [_measure_tree(item, depth + 1) for item in value.values()]
    elif isinstance(value, list):
        children = [_measure_tree(item, depth + 1) for item in value]
    else:
        return 1, depth
    return 1 + sum(item[0] for item in children), max((item[1] for item in children), default=depth)


def _select_precedence(loaded: list[_LoadedDocument]) -> tuple[_LoadedDocument, ...]:
    selected: dict[ResourceRef, _LoadedDocument] = {}
    seen_layer: set[tuple[int, str, str]] = set()
    resource_ids: dict[str, ResourceKind] = {}
    for item in loaded:
        identity = resource_identity(item.document)
        layer_key = (item.layer, identity.kind.value, identity.resource_id)
        if layer_key in seen_layer:
            raise _error(
                "configuration_duplicate_resource", "A source layer defines one resource identity more than once."
            )
        seen_layer.add(layer_key)
        previous_kind = resource_ids.setdefault(identity.resource_id, identity.kind)
        if previous_kind is not identity.kind:
            raise _error("configuration_resource_kind_conflict", "A resource ID is reused by an incompatible kind.")
        selected[identity] = item
    return tuple(
        sorted(
            selected.values(),
            key=lambda item: (
                resource_identity(item.document).kind.value,
                resource_identity(item.document).resource_id,
            ),
        )
    )


async def _normalize_document(item: _LoadedDocument, settings: ConfigurationSettings) -> ResourceDocument:
    document = item.document
    if isinstance(document, PromptDefinition):
        blocks: list[PromptBlock] = []
        for block in document.instruction_blocks:
            if block.source is None:
                blocks.append(block)
                continue
            parent = PurePosixPath(item.relative_path).parent
            relative = (parent / block.source).as_posix()
            selected = item.overlay.get(relative)
            if selected is None and relative in item.overlay:
                raise _error("configuration_source_invalid", "A referenced Prompt source was deleted.")
            path = _confined_path(item.root, item.root, relative)
            raw = selected if isinstance(selected, bytes) else await _stable_read(path, settings)
            try:
                content = raw.decode("utf-8")
                blocks.append(PromptBlock(content=content))
            except UnicodeDecodeError as exc:
                raise _error("configuration_document_invalid", "A Prompt source is not valid UTF-8.") from exc
            except ValidationError as exc:
                raise _error(
                    "configuration_document_invalid",
                    "A resolved Prompt source is invalid.",
                    details={"validation_error_count": exc.error_count()},
                ) from exc
        return document.model_copy(update={"instruction_blocks": tuple(blocks)}, deep=True)
    return document


def _confined_path(root: Path, parent: Path, relative: str) -> Path:
    path = parent.joinpath(*PurePosixPath(relative).parts)
    try:
        resolved_parent = path.parent.resolve(strict=True)
        resolved_parent.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise _error("configuration_source_invalid", "A referenced source path escapes its definition root.") from exc
    if path.is_symlink():
        raise _error("configuration_source_invalid", "Referenced source symlinks are forbidden.")
    return path


def _availability(
    settings: ConfigurationSettings,
) -> tuple[tuple[DependencyLock, ...], dict[str, DependencyLock], dict[str, DependencyLock]]:
    try:
        discovered_plugins = {item.plugin_key: item for item in discover_harness_plugin_factory_references()}
        plugin_catalog = build_harness_plugin_factory_catalog(plugin_keys=settings.plugin_keys)
        discovered_providers = {item.provider_key: item for item in discover_environment_provider_factory_references()}
        provider_catalog = build_environment_provider_factory_catalog(
            builtin_keys=settings.builtin_provider_keys,
            extension_keys=settings.extension_provider_keys,
        )
    except Exception as exc:
        raise _error("factory_catalog_invalid", "A selected installed factory catalog is unavailable.") from exc
    plugin_locks: dict[str, DependencyLock] = {}
    for registration in plugin_catalog.registrations:
        reference = discovered_plugins.get(registration.plugin_key)
        plugin_locks[registration.plugin_key] = DependencyLock(
            dependency_kind="harness_plugin",
            key=registration.plugin_key,
            distribution_name=registration.distribution_name or (reference.distribution_name if reference else None),
            distribution_version=registration.distribution_version
            or (reference.distribution_version if reference else None),
        )
    provider_locks: dict[str, DependencyLock] = {}
    for registration in provider_catalog.registrations:
        reference = discovered_providers.get(registration.provider_key)
        provider_locks[registration.provider_key] = DependencyLock(
            dependency_kind="environment_provider",
            key=registration.provider_key,
            distribution_name=registration.distribution_name or (reference.distribution_name if reference else None),
            distribution_version=registration.distribution_version
            or (reference.distribution_version if reference else None),
        )
    availability = tuple(
        sorted((*plugin_locks.values(), *provider_locks.values()), key=lambda item: (item.dependency_kind, item.key))
    )
    return availability, plugin_locks, provider_locks


def _validate_provider_binding(binding: Any, settings: ConfigurationSettings) -> None:
    parameters = binding.provider_parameters
    if binding.provider_key == "a13n.direct-local":
        root = parameters.get("root") if isinstance(parameters, dict) else None
        directory_id = root.get("directory_id") if isinstance(root, dict) else None
        directories = {item.directory_id: item.path for item in settings.local_directories}
        path = directories.get(directory_id) if isinstance(directory_id, str) else None
        if path is None or not isinstance(root, dict):
            raise _error(
                "provider_spec_invalid",
                "Direct Local provider roots must select an authorized directory alias.",
            )
        parameters = {
            **parameters,
            "root": {
                "path": str(path),
                "read_only": bool(root.get("read_only", False)),
            },
        }
    try:
        catalog = build_environment_provider_factory_catalog(
            builtin_keys=settings.builtin_provider_keys,
            extension_keys=settings.extension_provider_keys,
        )
        catalog.resolve_spec(
            EnvironmentProviderSpec(
                provider_key=binding.provider_key,
                schema_version=binding.provider_schema_version,
                parameters=parameters,
            )
        )
    except Exception as exc:
        raise _error("provider_spec_invalid", "An Environment provider specification is invalid.") from exc


def _validate_graph(revisions: list[ResourceRevision], identities: set[tuple[str, str]]) -> None:
    agent_edges: dict[str, set[str]] = {}
    for revision in revisions:
        if revision.ref.kind is not ResourceKind.agent:
            continue
        try:
            document = AgentDefinitionDocument.model_validate(revision.normalized_content, strict=True)
        except ValidationError as exc:
            raise _error("configuration_document_invalid", "An Agent revision is invalid.") from exc
        references = (
            document.model,
            document.prompt,
            *document.plugins,
            *document.skills.available,
            *(edge.agent for edge in document.subagents),
        )
        for reference in references:
            if (reference.kind.value, reference.resource_id) not in identities:
                raise _error(
                    "configuration_reference_missing", "A resource reference is missing or has the wrong kind."
                )
        agent_edges[revision.ref.resource_id] = {edge.agent.resource_id for edge in document.subagents}
    _reject_cycles(agent_edges)


def _reject_cycles(edges: dict[str, set[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise _error("agent_graph_cycle", "The Agent resource graph contains a cycle.")
        if node in visited:
            return
        visiting.add(node)
        for child in edges.get(node, set()):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in edges:
        visit(node)


async def _read_skill_package(
    document: SkillDefinition,
    settings: ConfigurationSettings,
    overlays: Mapping[str, Mapping[str, bytes | None]],
) -> JsonValue:
    roots = {root.root_id: root.path for root in settings.ordered_roots}
    root = roots.get(document.package.root_id)
    if root is None:
        raise _error("skill_package_unauthorized", "A Skill package selects an unauthorized definition root.")
    package_root = root.joinpath(*PurePosixPath(document.package.relative_path).parts)
    payload = await to_thread.run_sync(
        partial(
            _read_skill_package_sync,
            package_root,
            document.package.relative_path,
            overlays.get(document.package.root_id, {}),
            settings.max_skill_package_files,
            settings.max_skill_package_depth,
            settings.max_skill_package_bytes,
            settings.stable_read_attempts,
        )
    )
    if not isinstance(payload, dict):
        raise _error("skill_package_invalid", "A managed Skill package payload is invalid.")
    files_value = payload.get("files")
    if not isinstance(files_value, list):
        raise _error("skill_package_invalid", "A managed Skill package manifest is invalid.")
    files = cast(list[dict[str, JsonValue]], files_value)
    skill_document = next((item for item in files if item["path"] == "SKILL.md"), None)
    if skill_document is None:
        raise _error("skill_package_invalid", "A managed Skill package must contain SKILL.md.")
    copied: list[tuple[str, bytes]] = []
    try:
        for item in files:
            path = item["path"]
            encoded = item["content_base64"]
            if not isinstance(path, str) or not isinstance(encoded, str):
                raise ValueError("invalid package file")
            copied.append((path, base64.b64decode(encoded, validate=True)))
    except (KeyError, ValueError) as exc:
        raise _error("skill_package_invalid", "A managed Skill package payload is invalid.") from exc
    from .skills import validate_managed_skill_package

    await validate_managed_skill_package(
        tuple(copied),
        expected_name=document.skill_name,
        expected_description=document.description,
        settings=settings,
    )
    return payload


def _read_skill_package_sync(
    root: Path,
    package_relative: str,
    overlay: Mapping[str, bytes | None],
    max_files: int,
    max_depth: int,
    max_bytes: int,
    stable_read_attempts: int,
) -> JsonValue:
    content_by_path: dict[str, bytes] = {}
    if root.exists():
        for _attempt in range(stable_read_attempts):
            before = _package_fingerprints(root, max_files=max_files, max_depth=max_depth)
            try:
                candidate = {
                    relative: _stable_package_read(
                        root.joinpath(*PurePosixPath(relative).parts),
                        max_bytes=max_bytes,
                        attempts=1,
                    )
                    for relative in sorted(before)
                }
            except ConfigurationError as exc:
                if exc.code == "skill_package_unstable":
                    continue
                raise
            after = _package_fingerprints(root, max_files=max_files, max_depth=max_depth)
            if before == after:
                content_by_path = candidate
                break
        else:
            raise _error("skill_package_unstable", "A managed Skill package changed during bounded stable reads.")
    prefix = f"{package_relative.rstrip('/')}/"
    for source_relative, content in overlay.items():
        if not source_relative.startswith(prefix):
            continue
        relative = source_relative.removeprefix(prefix)
        relative_path = PurePosixPath(relative)
        if (
            not relative
            or any(part in {".", ".."} for part in relative_path.parts)
            or len(relative_path.parts) > max_depth
        ):
            raise _error("skill_package_invalid", "A Skill package path is invalid.")
        if content is None:
            content_by_path.pop(relative, None)
        else:
            content_by_path[relative] = content
    files: list[dict[str, JsonValue]] = []
    total = 0
    for relative, content in sorted(content_by_path.items()):
        total += len(content)
        if len(files) >= max_files or total > max_bytes:
            raise _error("skill_package_limit", "A managed Skill package exceeds configured limits.")
        files.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "content_base64": base64.b64encode(content).decode("ascii"),
            }
        )
    manifest = [{"path": item["path"], "sha256": item["sha256"]} for item in files]
    return cast(
        JsonValue,
        {
            "schema_version": "1",
            "package_digest": canonical_digest(manifest),
            "files": files,
        },
    )


def _package_fingerprints(
    root: Path,
    *,
    max_files: int,
    max_depth: int,
) -> dict[str, tuple[int, int, int, int]]:
    try:
        root_metadata = root.stat(follow_symlinks=False)
    except OSError as exc:
        raise _error("skill_package_unavailable", "A managed Skill package directory is unavailable.") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or root.is_symlink():
        raise _error("skill_package_invalid", "A managed Skill package root must be a real directory.")
    fingerprints: dict[str, tuple[int, int, int, int]] = {}
    entry_count = 0
    try:
        for current, directories, names in os.walk(
            root,
            followlinks=False,
            onerror=_raise_walk_error,
        ):
            directories.sort()
            names.sort()
            current_path = Path(current)
            for directory in directories:
                path = current_path / directory
                metadata = path.stat(follow_symlinks=False)
                if not stat.S_ISDIR(metadata.st_mode):
                    raise _error("skill_package_invalid", "Skill package symlinks are forbidden.")
                entry_count += 1
                relative = path.relative_to(root)
                if entry_count > max_files or len(relative.parts) > max_depth:
                    raise _error("skill_package_limit", "A managed Skill package exceeds configured limits.")
            for name in names:
                path = current_path / name
                metadata = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise _error(
                        "skill_package_invalid",
                        "Skill packages may contain only regular files and directories.",
                    )
                relative_path = path.relative_to(root)
                entry_count += 1
                if entry_count > max_files or len(relative_path.parts) > max_depth:
                    raise _error("skill_package_limit", "A managed Skill package exceeds configured limits.")
                relative = relative_path.as_posix()
                fingerprints[relative] = (
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                )
    except ConfigurationError:
        raise
    except OSError as exc:
        raise _error("skill_package_unavailable", "A managed Skill package changed during traversal.") from exc
    return fingerprints


def _raise_walk_error(error: OSError) -> None:
    raise error


def _stable_package_read(path: Path, *, max_bytes: int, attempts: int) -> bytes:
    for _attempt in range(attempts):
        try:
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise _error(
                    "skill_package_invalid",
                    "Skill packages may contain only regular files and directories.",
                )
            if before.st_size > max_bytes:
                raise _error("skill_package_limit", "A managed Skill package exceeds configured limits.")
            content = path.read_bytes()
            after = path.stat(follow_symlinks=False)
        except ConfigurationError:
            raise
        except OSError as exc:
            raise _error("skill_package_unavailable", "A managed Skill package file is unavailable.") from exc
        before_fingerprint = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        after_fingerprint = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if before_fingerprint == after_fingerprint and len(content) == before.st_size:
            return content
    raise _error("skill_package_unstable", "A managed Skill package changed during bounded stable reads.")


def _error(code: str, message: str, *, details: dict[str, Any] | None = None) -> ConfigurationError:
    return ConfigurationError(message, code=code, details=details)


__all__ = [
    "CatalogCandidate",
    "SkillPackageCandidate",
    "load_catalog_candidate",
    "load_configuration_settings",
]
