"""Git-backed declarative Content Plugin installation and catalog loading."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Self
from urllib.parse import urlsplit
from uuid import uuid4

import yaml
from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from a13n_ui.errors import ConfigurationError, ContentPluginError

_MARKETPLACE_PATH = Path(".agents/plugins/marketplace.yaml")
_MANIFEST_PATH = Path(".a13n-plugin/plugin.yaml")
_PLUGIN_ID_PATTERN = r"^plugin-[a-z0-9]+(?:-[a-z0-9]+)*$"
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_REGISTRATION_BYTES = 64 * 1024
_MAX_PLUGIN_FILES = 4096
_MAX_PLUGIN_FILE_BYTES = 16 * 1024 * 1024
_MAX_PLUGIN_BYTES = 64 * 1024 * 1024
_MAX_PLUGINS = 256
_MAX_YAML_NODES = 100_000
_MAX_YAML_DEPTH = 64
_GIT_TIMEOUT_SECONDS = 300


class _PluginModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, str_strip_whitespace=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_tuples(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        for name in ("plugins", "subagent_paths"):
            item = normalized.get(name)
            if isinstance(item, list):
                normalized[name] = tuple(item)
        return normalized


class ContentPluginMarketplaceEntry(_PluginModel):
    path: str = Field(min_length=3, max_length=4096)

    @field_validator("path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        _validate_relative_path(value)
        return value


class ContentPluginMarketplace(_PluginModel):
    schema_version: str = Field(pattern=r"^1$")
    name: str = Field(min_length=1, max_length=256)
    plugins: tuple[ContentPluginMarketplaceEntry, ...] = Field(min_length=1, max_length=_MAX_PLUGINS)

    @model_validator(mode="after")
    def _unique_paths(self) -> Self:
        paths = tuple(item.path for item in self.plugins)
        if len(paths) != len(set(paths)):
            raise ValueError("marketplace plugin paths must be unique")
        return self


class ContentPluginManifest(_PluginModel):
    schema_version: str = Field(pattern=r"^1$")
    kind: str = Field(pattern=r"^content_plugin$")
    id: str = Field(min_length=8, max_length=128, pattern=_PLUGIN_ID_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4096)
    skills: str | None = Field(default=None, min_length=3, max_length=4096)
    subagents: str | None = Field(default=None, min_length=3, max_length=4096)

    @model_validator(mode="after")
    def _valid_content(self) -> Self:
        if self.skills is None and self.subagents is None:
            raise ValueError("a Content Plugin must contribute skills or subagents")
        for value in (self.skills, self.subagents):
            if value is not None:
                _validate_relative_path(value)
        return self


class ContentPluginRegistration(_PluginModel):
    """Durable registration for one published immutable plugin object."""

    schema_version: str = Field(pattern=r"^1$")
    plugin_id: str = Field(min_length=8, max_length=128, pattern=_PLUGIN_ID_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4096)
    repository: str = Field(min_length=1, max_length=4096)
    commit: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class InstalledContentPlugin(_PluginModel):
    """Validated catalog entry with paths derived from the selected data root."""

    plugin_id: str = Field(min_length=8, max_length=128, pattern=_PLUGIN_ID_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4096)
    repository: str = Field(min_length=1, max_length=4096)
    commit: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    content_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str = Field(min_length=1, max_length=4096)
    skills_path: str | None = Field(default=None, min_length=1, max_length=4096)
    subagent_paths: tuple[str, ...] = Field(default=(), max_length=_MAX_PLUGIN_FILES)


class UninstalledContentPlugin(_PluginModel):
    """Result of removing one registration while retaining any published object."""

    plugin_id: str = Field(min_length=8, max_length=128, pattern=_PLUGIN_ID_PATTERN)
    retained_path: str | None = Field(default=None, min_length=1, max_length=4096)


class ContentPluginStore:
    """Manage per-plugin registrations and retained content-addressed objects."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root.expanduser()))
        self.installed = self.root / "installed"
        self.objects = self.root / "objects"
        self.staging = self.root / "staging"

    async def install(
        self,
        repository: str,
        *,
        plugin_id: str | None = None,
        ref: str | None = None,
    ) -> InstalledContentPlugin:
        return await to_thread.run_sync(self._install, repository, plugin_id, ref)

    async def list(self) -> tuple[InstalledContentPlugin, ...]:
        return await to_thread.run_sync(self._load_all)

    async def uninstall(self, plugin_id: str) -> UninstalledContentPlugin:
        return await to_thread.run_sync(self._uninstall, plugin_id)

    async def fingerprint(self) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
        return await to_thread.run_sync(self._registration_fingerprint)

    def _install(self, repository: str, plugin_id: str | None, ref: str | None) -> InstalledContentPlugin:
        selected_repository = _validate_repository(repository)
        selected_ref = _validate_ref(ref)
        selected_id = _validate_plugin_id(plugin_id) if plugin_id is not None else None
        _prepare_private_directories(self.root, self.installed, self.objects, self.staging)
        clone = self.staging / f"repository-{uuid4().hex}"
        published_object: Path | None = None
        try:
            _run_git(("clone", "--quiet", "--", selected_repository, os.fspath(clone)))
            if selected_ref is not None:
                _run_git(("-C", os.fspath(clone), "checkout", "--quiet", "--detach", selected_ref))
            commit = _run_git(("-C", os.fspath(clone), "rev-parse", "HEAD"), capture=True).strip()
            if not _is_commit(commit):
                raise _error("content_plugin_git_failed", "Git did not resolve a valid commit.")
            marketplace = _load_yaml_model(clone / _MARKETPLACE_PATH, ContentPluginMarketplace)
            candidates = tuple(_load_candidate(clone, item.path) for item in marketplace.plugins)
            candidate_ids = tuple(item[0].id for item in candidates)
            if len(candidate_ids) != len(set(candidate_ids)):
                raise _error(
                    "content_plugin_invalid",
                    "The marketplace contains duplicate Content Plugin IDs.",
                )
            if selected_id is None:
                if len(candidates) != 1:
                    raise _error(
                        "content_plugin_selection_required",
                        "The repository contains multiple plugins; select one with --plugin.",
                        plugin_ids=sorted(item[0].id for item in candidates),
                    )
                manifest, plugin_root = candidates[0]
            else:
                matches = tuple(item for item in candidates if item[0].id == selected_id)
                if not matches:
                    raise _error(
                        "content_plugin_not_found",
                        "The selected Content Plugin is not present in the repository.",
                        plugin_id=selected_id,
                    )
                manifest, plugin_root = matches[0]
            registration_path = self._registration_path(manifest.id)
            if registration_path.exists():
                raise _error(
                    "content_plugin_already_installed",
                    "The Content Plugin is already installed.",
                    plugin_id=manifest.id,
                )
            content_digest = _tree_digest(plugin_root)
            published_object = self.objects / content_digest
            _publish_tree(plugin_root, published_object, self.staging)
            registration = ContentPluginRegistration(
                schema_version="1",
                plugin_id=manifest.id,
                name=manifest.name,
                version=manifest.version,
                description=manifest.description,
                repository=selected_repository,
                commit=commit,
                content_digest=content_digest,
            )
            _publish_registration(registration_path, registration)
            try:
                return self._load_registration(registration_path)
            except BaseException:
                registration_path.unlink(missing_ok=True)
                raise
        finally:
            shutil.rmtree(clone, ignore_errors=True)
            if published_object is not None:
                _remove_empty_parent(self.staging)

    def _load_all(self) -> tuple[InstalledContentPlugin, ...]:
        if not _store_directory_exists(self.root) or not _store_directory_exists(self.installed):
            return ()
        registrations: list[InstalledContentPlugin] = []
        try:
            entries = sorted(self.installed.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            raise _error("content_plugin_store_unavailable", "The Content Plugin catalog cannot be listed.") from exc
        if len(entries) > _MAX_PLUGINS:
            raise _error("content_plugin_limit", "The Content Plugin catalog contains too many registrations.")
        for path in entries:
            if path.suffix != ".json":
                continue
            registrations.append(self._load_registration(path))
        ids = tuple(item.plugin_id for item in registrations)
        if len(ids) != len(set(ids)):
            raise _error("content_plugin_store_invalid", "The Content Plugin catalog contains duplicate IDs.")
        return tuple(registrations)

    def _load_registration(self, path: Path) -> InstalledContentPlugin:
        raw = _read_regular_file(path, _MAX_REGISTRATION_BYTES)
        try:
            registration = ContentPluginRegistration.model_validate_json(raw, strict=True)
        except (ValueError, ValidationError) as exc:
            raise _error(
                "content_plugin_store_invalid",
                "An installed Content Plugin registration is invalid.",
                path=os.fspath(path),
            ) from exc
        if path.name != f"{registration.plugin_id}.json":
            raise _error(
                "content_plugin_store_invalid",
                "A Content Plugin registration filename does not match its ID.",
                path=os.fspath(path),
            )
        objects_metadata = _lstat(self.objects)
        if stat.S_ISLNK(objects_metadata.st_mode) or not stat.S_ISDIR(objects_metadata.st_mode):
            raise _error("content_plugin_store_invalid", "The Content Plugin object root is invalid.")
        object_root = self.objects / registration.content_digest
        if object_root.parent != self.objects:
            raise _error("content_plugin_store_invalid", "A Content Plugin object path is invalid.")
        manifest = _load_yaml_model(object_root / _MANIFEST_PATH, ContentPluginManifest)
        if (
            manifest.id != registration.plugin_id
            or manifest.name != registration.name
            or manifest.version != registration.version
            or manifest.description != registration.description
        ):
            raise _error(
                "content_plugin_store_invalid",
                "A Content Plugin object does not match its registration.",
                plugin_id=registration.plugin_id,
            )
        if _tree_digest(object_root) != registration.content_digest:
            raise _error(
                "content_plugin_store_invalid",
                "A Content Plugin object does not match its content digest.",
                plugin_id=registration.plugin_id,
            )
        skills_path = _optional_content_directory(object_root, manifest.skills, kind="skills")
        subagents_root = _optional_content_directory(object_root, manifest.subagents, kind="subagents")
        if skills_path is not None:
            _validate_skills_root(skills_path)
        subagent_paths = () if subagents_root is None else _validate_subagents_root(subagents_root)
        return InstalledContentPlugin(
            plugin_id=registration.plugin_id,
            name=registration.name,
            version=registration.version,
            description=registration.description,
            repository=registration.repository,
            commit=registration.commit,
            content_digest=registration.content_digest,
            path=os.fspath(object_root),
            skills_path=None if skills_path is None else os.fspath(skills_path),
            subagent_paths=tuple(os.fspath(item) for item in subagent_paths),
        )

    def _uninstall(self, plugin_id: str) -> UninstalledContentPlugin:
        selected_id = _validate_plugin_id(plugin_id)
        filename = f"{selected_id}.json"
        retained_path: str | None = None
        directory_descriptor = -1
        file_descriptor = -1
        try:
            directory_descriptor = _open_store_subdirectory(self.root, "installed")
            metadata = os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise _error(
                    "content_plugin_store_invalid",
                    "The Content Plugin registration is not a regular file.",
                    plugin_id=selected_id,
                )
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            file_descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
            before = os.fstat(file_descriptor)
            raw = os.read(file_descriptor, _MAX_REGISTRATION_BYTES + 1)
            after = os.fstat(file_descriptor)
            if (
                len(raw) > _MAX_REGISTRATION_BYTES
                or len(raw) != before.st_size
                or _fingerprint(metadata) != _fingerprint(before)
                or _fingerprint(before) != _fingerprint(after)
            ):
                raise _error(
                    "content_plugin_store_invalid",
                    "The Content Plugin registration changed or exceeded its size limit.",
                    plugin_id=selected_id,
                )
            try:
                registration = ContentPluginRegistration.model_validate_json(raw, strict=True)
                if registration.plugin_id == selected_id:
                    retained_path = os.fspath(self.objects / registration.content_digest)
            except (ValueError, ValidationError):
                pass
            os.unlink(filename, dir_fd=directory_descriptor)
            os.fsync(directory_descriptor)
        except FileNotFoundError as exc:
            raise _error(
                "content_plugin_not_installed",
                "The Content Plugin is not installed.",
                plugin_id=selected_id,
            ) from exc
        except ContentPluginError:
            raise
        except OSError as exc:
            raise _error(
                "content_plugin_store_unavailable",
                "The Content Plugin registration could not be removed.",
                plugin_id=selected_id,
            ) from exc
        finally:
            if file_descriptor >= 0:
                os.close(file_descriptor)
            if directory_descriptor >= 0:
                os.close(directory_descriptor)
        return UninstalledContentPlugin(plugin_id=selected_id, retained_path=retained_path)

    def _registration_path(self, plugin_id: str) -> Path:
        return self.installed / f"{plugin_id}.json"

    def _registration_fingerprint(self) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
        if not _store_directory_exists(self.root) or not _store_directory_exists(self.installed):
            return ()
        values: list[tuple[str, tuple[int, int, int, int]]] = []
        try:
            entries = sorted(self.installed.iterdir(), key=lambda path: path.name)
            for item in entries:
                if item.suffix != ".json":
                    continue
                metadata = item.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise _error("content_plugin_store_invalid", "A Content Plugin registration is invalid.")
                values.append((item.name, _fingerprint(metadata)))
        except ContentPluginError:
            raise
        except OSError as exc:
            raise _error("content_plugin_store_unavailable", "The Content Plugin catalog cannot be listed.") from exc
        return tuple(values)


def _load_candidate(repository_root: Path, relative_path: str) -> tuple[ContentPluginManifest, Path]:
    plugin_root = _owned_path(repository_root, relative_path)
    metadata = _lstat(plugin_root)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error("content_plugin_invalid", "A marketplace entry must select a non-symlink directory.")
    manifest = _load_yaml_model(plugin_root / _MANIFEST_PATH, ContentPluginManifest)
    skills_path = _optional_content_directory(plugin_root, manifest.skills, kind="skills")
    subagents_path = _optional_content_directory(plugin_root, manifest.subagents, kind="subagents")
    _tree_digest(plugin_root)
    if skills_path is not None:
        _validate_skills_root(skills_path)
    if subagents_path is not None:
        _validate_subagents_root(subagents_path)
    return manifest, plugin_root


def _optional_content_directory(root: Path, value: str | None, *, kind: str) -> Path | None:
    if value is None:
        return None
    path = _owned_path(root, value)
    metadata = _lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error("content_plugin_invalid", f"The declared {kind} path must be a non-symlink directory.")
    return path


def _validate_skills_root(root: Path) -> None:
    entries = sorted(root.iterdir(), key=lambda item: item.name)
    skill_count = 0
    for item in entries:
        metadata = _lstat(item)
        if stat.S_ISLNK(metadata.st_mode):
            raise _error("content_plugin_invalid", "A Content Plugin Skill entry cannot be a symlink.")
        if not stat.S_ISDIR(metadata.st_mode):
            if item.name == "README.md" and stat.S_ISREG(metadata.st_mode):
                continue
            raise _error("content_plugin_invalid", "A Content Plugin Skill root contains an unsupported entry.")
        skill_document = item / "SKILL.md"
        document_metadata = _lstat(skill_document)
        if stat.S_ISLNK(document_metadata.st_mode) or not stat.S_ISREG(document_metadata.st_mode):
            raise _error("content_plugin_invalid", "A Content Plugin Skill directory requires SKILL.md.")
        skill_count += 1
    if skill_count == 0:
        raise _error("content_plugin_invalid", "A declared Content Plugin Skill root is empty.")


def _validate_subagents_root(root: Path) -> tuple[Path, ...]:
    from a13n_ui.configuration.loader import parse_canonical_markdown

    selected: list[Path] = []
    resource_ids: set[str] = set()
    for item in sorted(root.iterdir(), key=lambda path: path.name):
        metadata = _lstat(item)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise _error("content_plugin_invalid", "A Content Plugin subagent source must be a regular file.")
        if item.name == "README.md":
            continue
        if not item.name.endswith(".md") or item.name != item.name.lower():
            raise _error(
                "content_plugin_invalid",
                "A Content Plugin subagent source must use a lower-case .md filename.",
                path=os.fspath(item),
            )
        try:
            resource = parse_canonical_markdown(item, _read_regular_file(item, _MAX_MANIFEST_BYTES))
        except ConfigurationError as exc:
            raise _error(
                "content_plugin_invalid",
                "A Content Plugin subagent source is invalid.",
                path=os.fspath(item),
            ) from exc
        if resource.id in resource_ids:
            raise _error(
                "content_plugin_invalid",
                "A Content Plugin contributes duplicate subagent IDs.",
                subagent_id=resource.id,
            )
        resource_ids.add(resource.id)
        selected.append(item)
    if not selected:
        raise _error("content_plugin_invalid", "A declared Content Plugin subagent root is empty.")
    return tuple(selected)


def _owned_path(root: Path, relative: str) -> Path:
    _validate_relative_path(relative)
    candidate = root.joinpath(*PurePosixPath(relative[2:]).parts)
    resolved_root = root.resolve(strict=True)
    resolved = candidate.resolve(strict=True)
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise _error("content_plugin_path_invalid", "A Content Plugin path escapes its owner root.")
    current = root
    for part in PurePosixPath(relative[2:]).parts:
        current = current / part
        if stat.S_ISLNK(_lstat(current).st_mode):
            raise _error("content_plugin_path_invalid", "A Content Plugin path cannot resolve through a symlink.")
    return resolved


def _validate_relative_path(value: str) -> None:
    if "\x00" in value or "\\" in value or not value.startswith("./"):
        raise ValueError("Content Plugin paths must use canonical ./ POSIX-relative syntax")
    remainder = value[2:]
    path = PurePosixPath(remainder)
    if not remainder or path.is_absolute() or any(part in {"", ".", "..", "~"} for part in path.parts):
        raise ValueError("Content Plugin paths must remain inside their owner root")
    if value != f"./{path.as_posix()}":
        raise ValueError("Content Plugin paths must be canonical")


def _tree_digest(root: Path) -> str:
    root_metadata = _lstat(root)
    if stat.S_ISLNK(root_metadata.st_mode) or not stat.S_ISDIR(root_metadata.st_mode):
        raise _error("content_plugin_invalid", "A Content Plugin root must be a non-symlink directory.")
    digest = hashlib.sha256()
    file_count = 0
    total_bytes = 0
    for directory, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        directory_path = Path(directory)
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            metadata = _lstat(directory_path / name)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise _error("content_plugin_invalid", "A Content Plugin tree contains a non-directory entry.")
        for name in file_names:
            path = directory_path / name
            metadata = _lstat(path)
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                raise _error("content_plugin_invalid", "A Content Plugin tree contains a special file or symlink.")
            file_count += 1
            total_bytes += metadata.st_size
            if (
                file_count > _MAX_PLUGIN_FILES
                or metadata.st_size > _MAX_PLUGIN_FILE_BYTES
                or total_bytes > _MAX_PLUGIN_BYTES
            ):
                raise _error("content_plugin_limit", "A Content Plugin tree exceeds its size limits.")
            relative = path.relative_to(root).as_posix().encode()
            content = _read_regular_file(path, _MAX_PLUGIN_FILE_BYTES)
            digest.update(len(relative).to_bytes(4, "big"))
            digest.update(relative)
            digest.update(b"x" if metadata.st_mode & 0o111 else b"-")
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
    return digest.hexdigest()


def _publish_tree(source: Path, destination: Path, staging: Path) -> None:
    if destination.exists():
        if _tree_digest(destination) != destination.name:
            raise _error("content_plugin_store_invalid", "An existing Content Plugin object is invalid.")
        return
    candidate = staging / f"object-{destination.name}-{uuid4().hex}"
    try:
        shutil.copytree(source, candidate, symlinks=False)
        _make_private_tree(candidate)
        try:
            candidate.rename(destination)
        except FileExistsError:
            if _tree_digest(destination) != destination.name:
                raise _error("content_plugin_store_invalid", "An existing Content Plugin object is invalid.") from None
    finally:
        shutil.rmtree(candidate, ignore_errors=True)


def _publish_registration(path: Path, registration: ContentPluginRegistration) -> None:
    content = registration.model_dump_json(indent=2).encode() + b"\n"
    directory_descriptor = -1
    file_descriptor = -1
    temporary_name = f".{path.name}.{uuid4().hex}.tmp"
    published = False
    try:
        directory_descriptor = _open_store_subdirectory(path.parent.parent, path.parent.name)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        file_descriptor = os.open(temporary_name, flags, 0o600, dir_fd=directory_descriptor)
        with os.fdopen(file_descriptor, "wb", closefd=True) as output:
            file_descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.link(
            temporary_name,
            path.name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        published = True
        os.fsync(directory_descriptor)
    except FileExistsError as exc:
        raise _error(
            "content_plugin_already_installed",
            "The Content Plugin is already installed.",
            plugin_id=registration.plugin_id,
        ) from exc
    except OSError as exc:
        if published and directory_descriptor >= 0:
            try:
                os.unlink(path.name, dir_fd=directory_descriptor)
            except OSError:
                pass
        raise _error(
            "content_plugin_store_unavailable", "The Content Plugin registration could not be written."
        ) from exc
    finally:
        if file_descriptor >= 0:
            os.close(file_descriptor)
        if directory_descriptor >= 0:
            try:
                os.unlink(temporary_name, dir_fd=directory_descriptor)
            except FileNotFoundError:
                pass
            finally:
                os.close(directory_descriptor)


def _load_yaml_model[T: BaseModel](path: Path, model_type: type[T]) -> T:
    content = _read_regular_file(path, _MAX_MANIFEST_BYTES)
    try:
        text = content.decode("utf-8")
        if "\x00" in text:
            raise ValueError("NUL is forbidden")
        depth = 0
        nodes = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise ValueError("anchors and aliases are forbidden")
            tag = getattr(event, "tag", None)
            if tag is not None and not str(tag).startswith("tag:yaml.org,2002:"):
                raise ValueError("custom tags are forbidden")
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
                nodes += 1
            elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
            elif isinstance(event, yaml.events.ScalarEvent):
                nodes += 1
            if nodes > _MAX_YAML_NODES or depth > _MAX_YAML_DEPTH:
                raise ValueError("YAML exceeds structural limits")
        value = yaml.load(text, Loader=_UniqueSafeLoader)
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return model_type.model_validate_json(serialized, strict=True)
    except (RecursionError, TypeError, UnicodeError, ValueError, yaml.YAMLError, ValidationError) as exc:
        raise _error(
            "content_plugin_invalid",
            "A Content Plugin marketplace or manifest is invalid.",
            path=os.fspath(path),
        ) from exc


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_unique_mapping(loader: _UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key == "<<" or key in mapping:
            raise yaml.constructor.ConstructorError(None, None, "duplicate or merged YAML key", key_node.start_mark)
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


def _read_regular_file(path: Path, max_bytes: int) -> bytes:
    descriptor = -1
    try:
        metadata = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_size > max_bytes:
            raise _error("content_plugin_invalid", "A Content Plugin source is not a bounded regular file.")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        content = os.read(descriptor, max_bytes + 1)
        after = os.fstat(descriptor)
    except ContentPluginError:
        raise
    except (FileNotFoundError, OSError) as exc:
        raise _error(
            "content_plugin_invalid",
            "A required Content Plugin source cannot be read.",
            path=os.fspath(path),
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > max_bytes or _fingerprint(metadata) != _fingerprint(after) or len(content) != metadata.st_size:
        raise _error("content_plugin_invalid", "A Content Plugin source changed or exceeded its size limit.")
    return content


def _run_git(arguments: Sequence[str], *, capture: bool = False) -> str:
    try:
        result = subprocess.run(
            ("git", *arguments),
            check=True,
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT_SECONDS,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _error("content_plugin_git_failed", "The Content Plugin Git operation failed.") from exc
    return result.stdout if capture else ""


def _validate_repository(value: str) -> str:
    selected = value.strip()
    if not selected or len(selected) > 4096 or "\x00" in selected or selected.startswith("-"):
        raise _error("content_plugin_repository_invalid", "The Content Plugin repository locator is invalid.")
    parsed = urlsplit(selected)
    if parsed.scheme in {"http", "https"} and (parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise _error(
            "content_plugin_repository_invalid",
            "The repository locator cannot contain credentials, a query, or a fragment.",
        )
    return selected


def _validate_ref(value: str | None) -> str | None:
    if value is None:
        return None
    selected = value.strip()
    if not selected or len(selected) > 1024 or "\x00" in selected or selected.startswith("-"):
        raise _error("content_plugin_ref_invalid", "The Content Plugin Git ref is invalid.")
    return selected


def _validate_plugin_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(_PLUGIN_ID_PATTERN, value) or len(value) > 128:
        raise _error("content_plugin_id_invalid", "The Content Plugin ID is invalid.")
    return value


def _is_commit(value: str) -> bool:
    return len(value) in {40, 64} and all(character in "0123456789abcdef" for character in value)


def _lstat(path: Path) -> os.stat_result:
    try:
        return path.lstat()
    except OSError as exc:
        raise _error(
            "content_plugin_invalid",
            "A required Content Plugin path is unavailable.",
            path=os.fspath(path),
        ) from exc


def _store_directory_exists(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise _error("content_plugin_store_unavailable", "A Content Plugin storage directory is unavailable.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error("content_plugin_store_invalid", "A Content Plugin storage directory is invalid.")
    return True


def _open_store_subdirectory(root: Path, name: str) -> int:
    if not _store_directory_exists(root):
        raise FileNotFoundError(root)
    root_descriptor = -1
    try:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        root_descriptor = os.open(root, flags)
        metadata = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise _error("content_plugin_store_invalid", "A Content Plugin storage directory is invalid.")
        descriptor = os.open(name, flags, dir_fd=root_descriptor)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise _error("content_plugin_store_invalid", "A Content Plugin storage directory is invalid.")
        return descriptor
    except (FileNotFoundError, ContentPluginError):
        raise
    except OSError as exc:
        raise _error("content_plugin_store_unavailable", "A Content Plugin storage directory is unavailable.") from exc
    finally:
        if root_descriptor >= 0:
            os.close(root_descriptor)


def _prepare_private_directories(*paths: Path) -> None:
    for path in paths:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise _error("content_plugin_store_invalid", "A Content Plugin storage directory is invalid.")
        if os.name != "nt":
            path.chmod(0o700)


def _make_private_tree(root: Path) -> None:
    if os.name == "nt":
        return
    for directory, directory_names, file_names in os.walk(root):
        Path(directory).chmod(0o700)
        for name in directory_names:
            (Path(directory) / name).chmod(0o700)
        for name in file_names:
            path = Path(directory) / name
            mode = path.stat(follow_symlinks=False).st_mode
            path.chmod(0o700 if mode & 0o111 else 0o600)


def _remove_empty_parent(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int]:
    if os.name == "nt":
        return (0, 0, metadata.st_size, metadata.st_mtime_ns)
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _error(code: str, message: str, **details: Any) -> ContentPluginError:
    return ContentPluginError(message, code=code, details=details)


__all__ = [
    "ContentPluginManifest",
    "ContentPluginMarketplace",
    "ContentPluginRegistration",
    "ContentPluginStore",
    "InstalledContentPlugin",
    "UninstalledContentPlugin",
]
