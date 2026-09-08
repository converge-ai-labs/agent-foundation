"""Git-backed declarative Content Plugin installation and catalog loading."""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Self
from urllib.parse import urlsplit

import yaml
from a13n_logging import get_logger
from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from a13n_harness_ui.errors import ContentPluginError

_MARKETPLACE_PATH = Path(".agents/plugins/marketplace.yaml")
_MANIFEST_PATH = Path(".a13n-plugin/plugin.yaml")
_PLUGIN_ID_PATTERN = r"^plugin-[a-z0-9]+(?:-[a-z0-9]+)*$"
_MAX_MANIFEST_BYTES = 1024 * 1024
_ORIGIN_PATH = Path(".a13n-plugin/origin.json")
_LOGGER = get_logger(__name__)
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


class ContentPluginOrigin(_PluginModel):
    """Installation provenance, independent of the editable manifest."""

    repository: str = Field(min_length=1, max_length=4096)
    commit: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class InstalledContentPlugin(_PluginModel):
    """Current manifest values and editable directory paths."""

    plugin_id: str = Field(min_length=8, max_length=128, pattern=_PLUGIN_ID_PATTERN)
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4096)
    repository: str = ""
    commit: str = ""
    path: str = Field(min_length=1, max_length=4096)
    skills_path: str | None = Field(default=None, min_length=1, max_length=4096)
    subagent_paths: tuple[str, ...] = Field(default=(), max_length=_MAX_PLUGIN_FILES)


class UninstalledContentPlugin(_PluginModel):
    """The directory removed by uninstall, including any local edits."""

    plugin_id: str
    removed_path: str


class ContentPluginStore:
    """Install, discover, and delete ordinary plugin-ID directories."""

    def __init__(self, root: Path) -> None:
        self.root = Path(os.path.abspath(root.expanduser()))
        self.diagnostics: list[str] = []

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
        return await to_thread.run_sync(self._catalog_fingerprint)

    def _warn(self, path: Path, error: Exception) -> None:
        message = f"{path}: {error}"
        self.diagnostics.append(message)
        _LOGGER.warning("content_plugin_skipped", extra={"path": os.fspath(path), "reason": str(error)})

    def _install(self, repository: str, plugin_id: str | None, ref: str | None) -> InstalledContentPlugin:
        repository = _validate_repository(repository)
        ref = _validate_ref(ref)
        selected_id = None if plugin_id is None else _validate_plugin_id(plugin_id)
        self.root.mkdir(parents=True, exist_ok=True)
        _store_directory_exists(self.root)
        with tempfile.TemporaryDirectory(prefix=".install-", dir=self.root) as staging:
            clone = Path(staging) / "repository"
            _run_git(("clone", "--quiet", "--", repository, os.fspath(clone)))
            if ref is not None:
                _run_git(("-C", os.fspath(clone), "checkout", "--quiet", "--detach", ref))
            commit = _run_git(("-C", os.fspath(clone), "rev-parse", "HEAD"), capture=True).strip()
            origin = ContentPluginOrigin(repository=repository, commit=commit)
            marketplace = _load_yaml_model(clone / _MARKETPLACE_PATH, ContentPluginMarketplace)
            candidates: dict[str, tuple[ContentPluginManifest, Path]] = {}
            for entry in marketplace.plugins:
                try:
                    root = _owned_path(clone, entry.path)
                    manifest = _load_yaml_model(_owned_path(root, "./.a13n-plugin/plugin.yaml"), ContentPluginManifest)
                    if manifest.id in candidates:
                        raise _error("content_plugin_invalid", "Duplicate marketplace plugin ID.")
                    candidates[manifest.id] = (manifest, root)
                except (ContentPluginError, OSError) as exc:
                    self._warn(clone / entry.path, exc)
            if selected_id is None:
                if len(candidates) != 1:
                    raise _error(
                        "content_plugin_selection_required",
                        "Select a valid plugin with --plugin.",
                        plugin_ids=sorted(candidates),
                    )
                selected_id = next(iter(candidates))
            if selected_id not in candidates:
                raise _error("content_plugin_not_found", "The selected plugin has no valid manifest.")
            manifest, source = candidates[selected_id]
            destination = self.root / selected_id
            if os.path.lexists(destination):
                raise _error("content_plugin_already_installed", "The Content Plugin is already installed.")
            _validate_tree(source)
            candidate = Path(staging) / "plugin"
            shutil.copytree(source, candidate)
            (candidate / _ORIGIN_PATH).write_text(origin.model_dump_json(indent=2), encoding="utf-8")
            # The staged directory is complete before it becomes discoverable.
            try:
                candidate.rename(destination)
            except OSError as exc:
                raise _error("content_plugin_install_failed", "The plugin directory could not be published.") from exc
        return self._load_directory(destination)

    def _directories(self) -> tuple[Path, ...]:
        if not _store_directory_exists(self.root):
            return ()
        return tuple(
            sorted((p for p in self.root.iterdir() if re.fullmatch(_PLUGIN_ID_PATTERN, p.name)), key=lambda p: p.name)
        )

    def _load_all(self) -> tuple[InstalledContentPlugin, ...]:
        self.diagnostics = []
        loaded: list[InstalledContentPlugin] = []
        for legacy in (self.root / "installed", self.root / "objects"):
            if legacy.exists():
                self._warn(
                    legacy,
                    ValueError(
                        "Legacy plugin layout is not loaded; relocate edited payloads to plugin-ID directories or reinstall."
                    ),
                )
        for path in self._directories():
            try:
                loaded.append(self._load_directory(path))
            except (ContentPluginError, OSError, ValueError) as exc:
                self._warn(path, exc)
        return tuple(loaded)

    def _load_directory(self, root: Path) -> InstalledContentPlugin:
        if not _store_directory_exists(root):
            raise _error("content_plugin_invalid", "The plugin directory is missing.")
        manifest = _load_yaml_model(_owned_path(root, "./.a13n-plugin/plugin.yaml"), ContentPluginManifest)
        if manifest.id != root.name:
            raise _error("content_plugin_invalid", "The manifest ID must match its directory name.")
        origin = None
        if os.path.lexists(root / _ORIGIN_PATH):
            try:
                origin = ContentPluginOrigin.model_validate_json(
                    _read_regular_file(_owned_path(root, "./.a13n-plugin/origin.json"), _MAX_MANIFEST_BYTES)
                )
            except (ContentPluginError, OSError, ValueError) as exc:
                self._warn(root / _ORIGIN_PATH, exc)
        skills = self._content_directory(root, manifest.skills)
        subagents = self._content_directory(root, manifest.subagents)
        paths: list[str] = []
        if subagents is not None:
            for item in sorted(subagents.iterdir()):
                if item.suffix.lower() == ".md" and item.name.lower() != "readme.md":
                    if item.is_file() and not item.is_symlink():
                        paths.append(os.fspath(item))
        return InstalledContentPlugin(
            plugin_id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            repository="" if origin is None else origin.repository,
            commit="" if origin is None else origin.commit,
            path=os.fspath(root),
            skills_path=None if skills is None else os.fspath(skills),
            subagent_paths=tuple(paths),
        )

    def _content_directory(self, root: Path, relative: str | None) -> Path | None:
        if relative is None:
            return None
        try:
            path = _owned_path(root, relative)
            if not path.is_dir():
                raise _error("content_plugin_invalid", "A declared source is not a directory.")
            return path
        except (ContentPluginError, OSError) as exc:
            self._warn(root / relative, exc)
            return None

    def _uninstall(self, plugin_id: str) -> UninstalledContentPlugin:
        path = self.root / _validate_plugin_id(plugin_id)
        if not _store_directory_exists(self.root) or not os.path.lexists(path):
            raise _error("content_plugin_not_installed", "The Content Plugin is not installed.")
        # Remove the requested directory, never a symlink target. No manifest is required.
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path)
        return UninstalledContentPlugin(plugin_id=plugin_id, removed_path=os.fspath(path))

    def _catalog_fingerprint(self) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
        values: list[tuple[str, tuple[int, int, int, int]]] = []
        if _store_directory_exists(self.root):
            values.append((".", _fingerprint(self.root.lstat())))
        for root in self._directories():
            if root.is_symlink():
                values.append((root.name, _fingerprint(root.lstat())))
                continue
            for directory, dirs, files in os.walk(root, followlinks=False):
                current = Path(directory)
                dirs[:] = sorted(name for name in dirs if not (current / name).is_symlink())
                for path in (current, *(current / name for name in sorted(files))):
                    try:
                        values.append((path.relative_to(self.root).as_posix(), _fingerprint(path.lstat())))
                    except FileNotFoundError:
                        continue
        return tuple(values)


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


def _validate_tree(root: Path) -> None:
    """Check copy boundaries and size using metadata, without reading asset bytes."""
    count = total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            metadata = _lstat(Path(directory) / name)
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                raise _error("content_plugin_invalid", "A plugin tree contains a symlink or special file.")
            if stat.S_ISREG(metadata.st_mode):
                count += 1
                total += metadata.st_size
                if count > _MAX_PLUGIN_FILES or metadata.st_size > _MAX_PLUGIN_FILE_BYTES or total > _MAX_PLUGIN_BYTES:
                    raise _error("content_plugin_limit", "The plugin exceeds installation size limits.")


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


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int]:
    if os.name == "nt":
        return (0, 0, metadata.st_size, metadata.st_mtime_ns)
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _error(code: str, message: str, **details: Any) -> ContentPluginError:
    return ContentPluginError(message, code=code, details=details)


__all__ = [
    "ContentPluginManifest",
    "ContentPluginMarketplace",
    "ContentPluginOrigin",
    "ContentPluginStore",
    "InstalledContentPlugin",
    "UninstalledContentPlugin",
]
