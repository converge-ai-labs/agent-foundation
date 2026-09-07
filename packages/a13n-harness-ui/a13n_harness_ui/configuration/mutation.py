"""Compare-and-set mutation of Harness UI configuration source files."""

from __future__ import annotations

import errno
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
from pydantic import BaseModel, ConfigDict, Field

from a13n_harness_ui.errors import ConfigurationError

from .loader import load_harness_ui_configuration
from .models import LoadedHarnessUiConfiguration

_MAX_SOURCE_BYTES = 1024 * 1024
_STABLE_READ_ATTEMPTS = 3
_RESOURCE_DIRECTORIES = frozenset({"models", "extensions", "mcp", "agents", "projects"})

type CandidateValidator = Callable[[LoadedHarnessUiConfiguration], None]


class ResourceMutationRequest(BaseModel):
    """Content and source-content precondition for a create or update."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    expected_source_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content: str


@dataclass(frozen=True, slots=True)
class ConfigurationMutationResult:
    """Verified configuration generation produced by one source mutation."""

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
    """Create or update one source using an exact digest compare-and-set."""

    selected, normalized, target = await to_thread.run_sync(
        _select_target,
        configuration_path,
        relative_path,
        False,
    )
    content = _encode_content(request.content, target)
    expected = _validate_expected_digest(request.expected_source_digest)
    latest = await to_thread.run_sync(_source_digest_or_none, target)
    _check_precondition(expected, latest, target)

    baseline = await load_harness_ui_configuration(selected, content_plugin_root=content_plugin_root)
    _check_loaded_target(baseline, normalized, expected, target)
    candidate = await _validate_candidate(
        baseline,
        selected,
        normalized,
        content,
        content_plugin_root=content_plugin_root,
    )
    if validate_candidate is not None:
        validate_candidate(candidate)

    # Validation can take time. Re-read the complete tree immediately before
    # publication so a candidate is never applied to a different generation.
    latest = await to_thread.run_sync(_source_digest_or_none, target)
    _check_precondition(expected, latest, target)
    current = await load_harness_ui_configuration(selected, content_plugin_root=content_plugin_root)
    if current.source_digest != baseline.source_digest:
        latest = await to_thread.run_sync(_source_digest_or_none, target)
        raise _conflict(target, expected, latest, "The configuration generation changed before publication.")

    desired_digest = hashlib.sha256(content).hexdigest()
    if latest == desired_digest:
        verified = await _verify_publication(
            selected,
            normalized,
            content,
            candidate,
            content_plugin_root=content_plugin_root,
        )
        return ConfigurationMutationResult("unchanged", normalized, desired_digest, verified)

    action: Literal["created", "updated"] = "created" if expected is None else "updated"
    await to_thread.run_sync(_publish_content, target, content, expected)
    verified = await _verify_publication(
        selected,
        normalized,
        content,
        candidate,
        content_plugin_root=content_plugin_root,
    )
    return ConfigurationMutationResult(action, normalized, desired_digest, verified)


async def delete_configuration_source(
    configuration_path: Path,
    relative_path: str,
    *,
    expected_source_digest: str,
    validate_candidate: CandidateValidator | None = None,
    content_plugin_root: Path | None = None,
) -> ConfigurationMutationResult:
    """Delete one non-root source using an exact digest compare-and-set."""

    selected, normalized, target = await to_thread.run_sync(
        _select_target,
        configuration_path,
        relative_path,
        True,
    )
    expected = _validate_expected_digest(expected_source_digest)
    if expected is None:  # Kept explicit for callers bypassing static typing.
        raise _error("configuration_mutation_invalid", "Deletion requires a source digest.", target)
    latest = await to_thread.run_sync(_source_digest_or_none, target)
    _check_precondition(expected, latest, target)

    baseline = await load_harness_ui_configuration(selected, content_plugin_root=content_plugin_root)
    _check_loaded_target(baseline, normalized, expected, target)
    candidate = await _validate_candidate(
        baseline,
        selected,
        normalized,
        None,
        content_plugin_root=content_plugin_root,
    )
    if validate_candidate is not None:
        validate_candidate(candidate)

    latest = await to_thread.run_sync(_source_digest_or_none, target)
    _check_precondition(expected, latest, target)
    current = await load_harness_ui_configuration(selected, content_plugin_root=content_plugin_root)
    if current.source_digest != baseline.source_digest:
        latest = await to_thread.run_sync(_source_digest_or_none, target)
        raise _conflict(target, expected, latest, "The configuration generation changed before publication.")

    await to_thread.run_sync(_delete_content, target, expected)
    verified = await _verify_publication(
        selected,
        normalized,
        None,
        candidate,
        content_plugin_root=content_plugin_root,
    )
    return ConfigurationMutationResult("deleted", normalized, None, verified)


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
        if not (yaml_source or markdown_source):
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


def _validate_expected_digest(value: str | None) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ConfigurationError(
            "Expected source digest must be a lower-case SHA-256 digest.",
            code="configuration_mutation_invalid",
        )
    return value


def _check_precondition(expected: str | None, latest: str | None, path: Path) -> None:
    if expected is None:
        if latest is not None:
            raise _conflict(path, expected, latest, "The destination already exists.")
        return
    if latest != expected:
        raise _conflict(path, expected, latest, "The configuration source is stale or missing.")


def _check_loaded_target(
    loaded: LoadedHarnessUiConfiguration,
    relative_path: str,
    expected: str | None,
    path: Path,
) -> None:
    try:
        source = loaded.source(relative_path)
    except KeyError:
        source = None
    latest = source.source_digest if source is not None else None
    _check_precondition(expected, latest, path)


async def _validate_candidate(
    baseline: LoadedHarnessUiConfiguration,
    configuration_path: Path,
    relative_path: str,
    replacement: bytes | None,
    *,
    content_plugin_root: Path | None,
) -> LoadedHarnessUiConfiguration:
    staging = await to_thread.run_sync(_stage_candidate, baseline, configuration_path, relative_path, replacement)
    try:
        return await load_harness_ui_configuration(
            staging / configuration_path.name,
            content_plugin_root=content_plugin_root,
        )
    finally:
        await to_thread.run_sync(shutil.rmtree, staging, True)


def _stage_candidate(
    baseline: LoadedHarnessUiConfiguration,
    configuration_path: Path,
    relative_path: str,
    replacement: bytes | None,
) -> Path:
    staging = Path(tempfile.mkdtemp(prefix=".a13n-harness-ui-candidate-", dir=configuration_path.parent))
    try:
        for source in baseline.sources:
            if source.relative_path.startswith(("content-plugins/", "built-in-subagents/")):
                continue
            if source.relative_path == relative_path:
                if replacement is None:
                    continue
                content = replacement
            else:
                source_path = configuration_path.parent.joinpath(*PurePosixPath(source.relative_path).parts)
                content = _source_bytes_with_digest(source_path, source.source_digest)
            destination = staging.joinpath(*PurePosixPath(source.relative_path).parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(content)
        if replacement is not None and all(source.relative_path != relative_path for source in baseline.sources):
            destination = staging.joinpath(*PurePosixPath(relative_path).parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(replacement)
        return staging
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _publish_content(path: Path, content: bytes, expected: str | None) -> None:
    _ensure_destination_parent(path.parent)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        latest = _source_digest_or_none(path)
        _check_precondition(expected, latest, path)
        if expected is None:
            try:
                os.link(temporary, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise _conflict(path, None, _source_digest_or_none(path), "The destination already exists.") from exc
            except OSError as exc:
                if exc.errno == errno.EEXIST:
                    raise _conflict(
                        path, None, _source_digest_or_none(path), "The destination already exists."
                    ) from exc
                raise
        else:
            os.replace(temporary, path)
        _fsync_directory(path.parent)
    except ConfigurationError:
        raise
    except OSError as exc:
        raise _error("configuration_mutation_failed", "The configuration source could not be published.", path) from exc
    finally:
        temporary.unlink(missing_ok=True)


def _delete_content(path: Path, expected: str) -> None:
    try:
        latest = _source_digest_or_none(path)
        _check_precondition(expected, latest, path)
        path.unlink()
        _fsync_directory(path.parent)
    except ConfigurationError:
        raise
    except OSError as exc:
        raise _error("configuration_mutation_failed", "The configuration source could not be deleted.", path) from exc


async def _verify_publication(
    configuration_path: Path,
    relative_path: str,
    replacement: bytes | None,
    candidate: LoadedHarnessUiConfiguration,
    *,
    content_plugin_root: Path | None,
) -> LoadedHarnessUiConfiguration:
    target = configuration_path.parent.joinpath(*PurePosixPath(relative_path).parts)
    latest = await to_thread.run_sync(_source_digest_or_none, target)
    expected = hashlib.sha256(replacement).hexdigest() if replacement is not None else None
    if latest != expected:
        raise ConfigurationError(
            "The final configuration source differs from the published bytes.",
            code="configuration_post_verify_failed",
            details={
                "path": str(target)[-4096:],
                "expected_source_digest": expected,
                "latest_source_digest": latest,
            },
        )
    try:
        loaded = await load_harness_ui_configuration(
            configuration_path,
            content_plugin_root=content_plugin_root,
        )
    except ConfigurationError as exc:
        raise ConfigurationError(
            "The published configuration generation did not verify.",
            code="configuration_post_verify_failed",
            details={"path": str(target)[-4096:], "latest_source_digest": latest},
        ) from exc
    if loaded.source_digest != candidate.source_digest:
        raise ConfigurationError(
            "The published configuration generation differs from the validated candidate.",
            code="configuration_post_verify_failed",
            details={
                "path": str(target)[-4096:],
                "expected_generation_digest": candidate.source_digest,
                "latest_generation_digest": loaded.source_digest,
                "latest_source_digest": latest,
            },
        )
    return loaded


def _ensure_destination_parent(parent: Path) -> None:
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = parent.lstat()
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error(
            "configuration_mutation_invalid",
            "A configuration source directory must be a non-symlink directory.",
            parent,
        )


def _source_bytes_with_digest(path: Path, expected_digest: str) -> bytes:
    """Read exact stable bytes and reject a generation change during staging."""

    for _attempt in range(_STABLE_READ_ATTEMPTS):
        try:
            before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_SOURCE_BYTES:
                raise _error(
                    "configuration_source_invalid",
                    "A configuration source must be a bounded non-symlink regular file.",
                    path,
                )
            content = path.read_bytes()
            after = path.stat(follow_symlinks=False)
        except ConfigurationError:
            raise
        except OSError as exc:
            raise _error("settings_unavailable", "A configuration source cannot be read.", path) from exc
        if _fingerprint(before) == _fingerprint(after) and len(content) == before.st_size:
            digest = hashlib.sha256(content).hexdigest()
            if digest != expected_digest:
                raise _conflict(
                    path,
                    expected_digest,
                    digest,
                    "The configuration generation changed during candidate staging.",
                )
            return content
    raise _error("settings_source_unstable", "A configuration source changed during bounded reads.", path)


def _source_digest_or_none(path: Path) -> str | None:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _error("settings_unavailable", "A configuration source cannot be inspected.", path) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise _error(
            "configuration_source_invalid",
            "A configuration source must be a non-symlink regular file.",
            path,
        )
    for _attempt in range(_STABLE_READ_ATTEMPTS):
        descriptor = -1
        try:
            before_path = path.stat(follow_symlinks=False)
            if before_path.st_size > _MAX_SOURCE_BYTES:
                raise _error("configuration_source_limit", "A configuration source exceeds its size limit.", path)
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            content = os.read(descriptor, _MAX_SOURCE_BYTES + 1)
            after = os.fstat(descriptor)
            after_path = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None
        except ConfigurationError:
            raise
        except OSError as exc:
            raise _error("settings_unavailable", "A configuration source cannot be read.", path) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(content) > _MAX_SOURCE_BYTES:
            raise _error("configuration_source_limit", "A configuration source exceeds its size limit.", path)
        fingerprints = tuple(_fingerprint(item) for item in (before_path, before, after, after_path))
        if len(set(fingerprints)) == 1 and len(content) == before.st_size:
            return hashlib.sha256(content).hexdigest()
    raise _error("settings_source_unstable", "A configuration source changed during bounded reads.", path)


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int]:
    if os.name == "nt":
        return (0, 0, metadata.st_size, metadata.st_mtime_ns)
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _conflict(path: Path, expected: str | None, latest: str | None, message: str) -> ConfigurationError:
    return ConfigurationError(
        message,
        code="configuration_source_conflict",
        details={
            "path": str(path)[-4096:],
            "expected_source_digest": expected,
            "latest_source_digest": latest,
        },
    )


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
