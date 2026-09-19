"""Validated last-write-wins mutation of Harness UI configuration source files."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from anyio import to_thread
from pydantic import BaseModel, ConfigDict

from a13n_harness_ui.errors import ConfigurationError

from .loader import _MAX_TOTAL_BYTES, _read_bounded_stable, _scan_tree, load_harness_ui_configuration
from .models import LoadedHarnessUiConfiguration

_MAX_SOURCE_BYTES = 1024 * 1024
_RESOURCE_DIRECTORIES = frozenset({"models", "extensions", "mcp", "agents", "projects", "devices"})

type CandidateValidator = Callable[[LoadedHarnessUiConfiguration], None]


class ResourceMutationRequest(BaseModel):
    """Replacement content for a create or update."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    content: str


@dataclass(frozen=True, slots=True)
class ConfigurationMutationResult:
    """Completed source write and the subsequently loaded configuration."""

    action: Literal["created", "updated", "deleted", "unchanged"]
    relative_path: str
    source_digest: str | None
    configuration: LoadedHarnessUiConfiguration


async def mutate_configuration_source(
    configuration_path: Path,
    relative_path: str,
    request: ResourceMutationRequest,
    *,
    validate_candidate: CandidateValidator | None = None,
    content_plugin_root: Path | None = None,
) -> ConfigurationMutationResult:
    """Validate and atomically replace one source; the last write wins."""

    selected, normalized, target = await to_thread.run_sync(
        _select_target,
        configuration_path,
        relative_path,
        False,
    )
    content = _encode_content(request.content, target)
    candidate = await _validate_candidate(
        selected,
        normalized,
        content,
        content_plugin_root=content_plugin_root,
    )
    if validate_candidate is not None:
        validate_candidate(candidate)

    action: Literal["created", "updated"] = "updated" if await to_thread.run_sync(target.exists) else "created"
    await to_thread.run_sync(_publish_content, target, content)
    loaded = await load_harness_ui_configuration(selected, content_plugin_root=content_plugin_root)
    return ConfigurationMutationResult(action, normalized, hashlib.sha256(content).hexdigest(), loaded)


async def validate_configuration_source(
    configuration_path: Path,
    relative_path: str,
    request: ResourceMutationRequest,
    *,
    validate_candidate: CandidateValidator | None = None,
    content_plugin_root: Path | None = None,
) -> LoadedHarnessUiConfiguration:
    """Validate replacement against the complete current tree without publishing it."""
    selected, normalized, target = await to_thread.run_sync(_select_target, configuration_path, relative_path, False)
    content = _encode_content(request.content, target)
    candidate = await _validate_candidate(selected, normalized, content, content_plugin_root=content_plugin_root)
    if validate_candidate is not None:
        validate_candidate(candidate)
    return candidate


async def delete_configuration_source(
    configuration_path: Path,
    relative_path: str,
    *,
    validate_candidate: CandidateValidator | None = None,
    content_plugin_root: Path | None = None,
) -> ConfigurationMutationResult:
    """Validate removal and delete the current non-root source, if present."""

    selected, normalized, target = await to_thread.run_sync(
        _select_target,
        configuration_path,
        relative_path,
        True,
    )
    candidate = await _validate_candidate(
        selected,
        normalized,
        None,
        content_plugin_root=content_plugin_root,
    )
    if validate_candidate is not None:
        validate_candidate(candidate)

    await to_thread.run_sync(_delete_content, target)
    loaded = await load_harness_ui_configuration(selected, content_plugin_root=content_plugin_root)
    return ConfigurationMutationResult("deleted", normalized, None, loaded)


# Concise aliases for transport/application layers that use resource terminology.
mutate_resource_source = mutate_configuration_source
delete_resource_source = delete_configuration_source


def _select_target(
    configuration_path: Path,
    relative_path: str,
    deleting: bool,
) -> tuple[Path, str, Path]:
    selected = configuration_path.expanduser().resolve(strict=False)
    if selected.suffix != ".yaml":
        raise _error(
            "configuration_mutation_invalid",
            "Harness UI configuration must use lower-case .yaml.",
            selected,
        )
    if not isinstance(relative_path, str) or not relative_path or len(relative_path) > 4096:
        raise _error("configuration_mutation_invalid", "The configuration source path is invalid.", selected)
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or relative.as_posix() != relative_path
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        raise _error("configuration_mutation_invalid", "The configuration source path is invalid.", selected)

    if relative_path == selected.name:
        if deleting:
            raise _error("configuration_mutation_invalid", "The root configuration cannot be deleted.", selected)
    elif len(relative.parts) != 2:
        raise _error("configuration_mutation_invalid", "Only immediate configuration sources are mutable.", selected)
    else:
        directory, filename = relative.parts
        yaml_source = directory in _RESOURCE_DIRECTORIES and filename.endswith(".yaml")
        markdown_source = directory == "subagents" and filename.endswith(".md") and filename != "README.md"
        json_source = directory == "mcp" and filename.endswith(".json")
        if not (yaml_source or markdown_source or json_source):
            raise _error("configuration_mutation_invalid", "The configuration source path is invalid.", selected)

    target = selected.parent.joinpath(*relative.parts)
    return selected, relative.as_posix(), target


def _encode_content(content: str, path: Path) -> bytes:
    if not isinstance(content, str) or "\x00" in content:
        raise _error("configuration_mutation_invalid", "Configuration content must be NUL-free UTF-8 text.", path)
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise _error(
            "configuration_mutation_invalid",
            "Configuration content must be valid UTF-8 text.",
            path,
        ) from exc
    if len(encoded) > _MAX_SOURCE_BYTES:
        raise _error("configuration_source_limit", "A configuration source exceeds its size limit.", path)
    return encoded


async def _validate_candidate(
    configuration_path: Path,
    relative_path: str,
    replacement: bytes | None,
    *,
    content_plugin_root: Path | None,
) -> LoadedHarnessUiConfiguration:
    staging = await to_thread.run_sync(_stage_candidate, configuration_path, relative_path, replacement)
    try:
        return await load_harness_ui_configuration(
            staging / configuration_path.name,
            content_plugin_root=content_plugin_root,
        )
    finally:
        await to_thread.run_sync(shutil.rmtree, staging, True)


def _stage_candidate(
    configuration_path: Path,
    relative_path: str,
    replacement: bytes | None,
) -> Path:
    staging = Path(tempfile.mkdtemp(prefix=".a13n-harness-ui-candidate-", dir=configuration_path.parent))
    try:
        # Validate the resulting tree, not the broken source being replaced or removed.
        # The loader's scan/read limits and membership checks still apply.
        sources = _scan_tree(configuration_path)
        total_bytes = 0
        for relative, source_path, expected in sources:
            if relative == relative_path:
                if replacement is None:
                    continue
                content = replacement
            else:
                content, fingerprint = _read_bounded_stable(source_path, _MAX_SOURCE_BYTES)
                if fingerprint != expected:
                    raise _error(
                        "settings_source_unstable", "Configuration changed during validation; retry.", source_path
                    )
            total_bytes += len(content)
            if total_bytes > _MAX_TOTAL_BYTES:
                raise _error(
                    "configuration_source_limit", "Configuration candidate exceeds its size limit.", configuration_path
                )
            destination = staging.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(content)
        if sources != _scan_tree(configuration_path):
            raise _error(
                "settings_source_unstable", "Configuration changed during validation; retry.", configuration_path
            )
        if replacement is not None and all(relative != relative_path for relative, _, _ in sources):
            destination = staging.joinpath(*PurePosixPath(relative_path).parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(replacement)
        return staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _publish_content(path: Path, content: bytes) -> None:
    _ensure_destination_parent(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except ConfigurationError:
        raise
    except OSError as exc:
        raise _error("configuration_mutation_failed", "The configuration source could not be published.", path) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _delete_content(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        if path.parent.exists():
            _fsync_directory(path.parent)
    except ConfigurationError:
        raise
    except OSError as exc:
        raise _error("configuration_mutation_failed", "The configuration source could not be deleted.", path) from exc


def _ensure_destination_parent(parent: Path) -> None:
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = parent.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error(
            "configuration_mutation_invalid",
            "A configuration source directory must be a non-symlink directory.",
            parent,
        )


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _error(code: str, message: str, path: Path) -> ConfigurationError:
    return ConfigurationError(message, code=code, details={"path": str(path)[-4096:]})


__all__ = [
    "CandidateValidator",
    "ConfigurationMutationResult",
    "ResourceMutationRequest",
    "delete_configuration_source",
    "delete_resource_source",
    "mutate_configuration_source",
    "mutate_resource_source",
]
