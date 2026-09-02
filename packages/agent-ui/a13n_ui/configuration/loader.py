"""Bounded loading for one Agent UI YAML document and sibling Markdown set."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

import yaml
from anyio import to_thread
from pydantic import ValidationError

from a13n_ui.errors import ConfigurationError

from .models import (
    AgentUiDocument,
    CanonicalSubagent,
    CanonicalSubagentSource,
    LoadedAgentUiConfiguration,
    canonical_digest,
)

_MAX_YAML_BYTES = 1024 * 1024
_MAX_MARKDOWN_BYTES = 1024 * 1024
_MAX_MARKDOWN_TOTAL_BYTES = 16 * 1024 * 1024
_MAX_MARKDOWN_FILES = 1024
_MAX_DIRECTORY_ENTRIES = 4096
_MAX_YAML_NODES = 100_000
_MAX_YAML_DEPTH = 64
_STABLE_READ_ATTEMPTS = 3


async def load_agent_ui_configuration(path: Path) -> LoadedAgentUiConfiguration:
    """Read one coherent source candidate for later trusted catalog resolution."""

    selected = path.expanduser().resolve(strict=False)
    if selected.suffix.lower() not in {".yaml", ".yml"}:
        raise _error("settings_path_invalid", "Agent UI configuration must use YAML.", selected)

    for _attempt in range(_STABLE_READ_ATTEMPTS):
        yaml_content, yaml_fingerprint = await to_thread.run_sync(
            _read_bounded_stable,
            selected,
            _MAX_YAML_BYTES,
        )
        before = await to_thread.run_sync(_scan_subagent_directory, selected.parent / "subagents")
        sources: list[CanonicalSubagentSource] = []
        total_bytes = 0
        source_changed = False
        for item in before:
            content, fingerprint = await to_thread.run_sync(
                _read_bounded_stable,
                item[0],
                _MAX_MARKDOWN_BYTES,
            )
            if fingerprint != item[1:]:
                source_changed = True
                break
            total_bytes += len(content)
            if total_bytes > _MAX_MARKDOWN_TOTAL_BYTES:
                raise _error(
                    "configuration_source_limit",
                    "Canonical subagent Markdown exceeds the total size limit.",
                    selected,
                )
            document = _parse_canonical_markdown(item[0], content)
            try:
                sources.append(
                    CanonicalSubagentSource(
                        document=document,
                        relative_path=f"subagents/{item[0].name}",
                        source_digest=hashlib.sha256(content).hexdigest(),
                    )
                )
            except ValidationError as exc:
                raise _validation_error(
                    "configuration_markdown_invalid",
                    "A canonical subagent source is invalid.",
                    item[0],
                    exc,
                ) from exc
        if source_changed:
            continue

        after = await to_thread.run_sync(_scan_subagent_directory, selected.parent / "subagents")
        final_yaml_fingerprint = await to_thread.run_sync(_regular_file_fingerprint, selected)
        if before != after or yaml_fingerprint != final_yaml_fingerprint:
            continue

        document = _parse_agent_ui_document(selected, yaml_content)
        yaml_digest = hashlib.sha256(yaml_content).hexdigest()
        source_digest = canonical_digest(
            {
                "yaml": yaml_digest,
                "subagents": [
                    {"name": item.document.name, "source_digest": item.source_digest}
                    for item in sorted(sources, key=lambda source: source.document.name)
                ],
            }
        )
        try:
            return LoadedAgentUiConfiguration(
                document=document,
                yaml_digest=yaml_digest,
                subagents=tuple(sorted(sources, key=lambda source: source.document.name)),
                source_digest=source_digest,
            )
        except ValidationError as exc:
            raise _validation_error(
                "configuration_invalid",
                "Agent UI configuration references are invalid.",
                selected,
                exc,
            ) from exc

    raise _error(
        "settings_source_unstable",
        "Agent UI configuration sources changed during bounded reads.",
        selected,
    )


def empty_agent_ui_configuration() -> LoadedAgentUiConfiguration:
    """Return the onboarding configuration used when the fixed default file is absent."""

    document = AgentUiDocument()
    yaml_digest = hashlib.sha256(b"").hexdigest()
    source_digest = canonical_digest({"yaml": yaml_digest, "subagents": []})
    return LoadedAgentUiConfiguration(
        document=document,
        yaml_digest=yaml_digest,
        source_digest=source_digest,
    )


def _read_bounded_stable(path: Path, max_bytes: int) -> tuple[bytes, tuple[int, int, int, int]]:
    for _attempt in range(_STABLE_READ_ATTEMPTS):
        descriptor = -1
        try:
            path_before = path.stat(follow_symlinks=False)
            if not stat.S_ISREG(path_before.st_mode):
                raise _error(
                    "configuration_source_invalid",
                    "A configuration source must be a non-symlink regular file.",
                    path,
                )
            flags = os.O_RDONLY
            flags |= getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            descriptor = os.open(path, flags)
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
                raise _error(
                    "configuration_source_limit",
                    "A configuration source exceeds its size limit.",
                    path,
                )
            remaining = max_bytes + 1
            chunks: list[bytes] = []
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
            path_after = path.stat(follow_symlinks=False)
        except ConfigurationError:
            raise
        except OSError as exc:
            raise _error("settings_unavailable", "A selected configuration source cannot be read.", path) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        if len(content) > max_bytes:
            raise _error("configuration_source_limit", "A configuration source exceeds its size limit.", path)
        fingerprint_before = _fingerprint(before)
        if (
            fingerprint_before == _fingerprint(after)
            and fingerprint_before == _fingerprint(path_before)
            and fingerprint_before == _fingerprint(path_after)
            and len(content) == before.st_size
        ):
            return content, fingerprint_before
    raise _error("settings_source_unstable", "A configuration source changed during bounded reads.", path)


def _regular_file_fingerprint(path: Path) -> tuple[int, int, int, int]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise _error("settings_unavailable", "A selected configuration source cannot be read.", path) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise _error(
            "configuration_source_invalid",
            "A configuration source must be a non-symlink regular file.",
            path,
        )
    return _fingerprint(metadata)


def _fingerprint(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)


def _scan_subagent_directory(directory: Path) -> tuple[tuple[Path, int, int, int, int], ...]:
    try:
        metadata = directory.lstat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise _error("settings_unavailable", "The canonical subagent directory cannot be read.", directory) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise _error(
            "configuration_source_invalid",
            "The canonical subagent source must be a non-symlink directory.",
            directory,
        )
    selected: list[tuple[Path, int, int, int, int]] = []
    try:
        with os.scandir(directory) as entries:
            entry_count = 0
            for entry in entries:
                entry_count += 1
                if entry_count > _MAX_DIRECTORY_ENTRIES:
                    raise _error(
                        "configuration_source_limit",
                        "The canonical subagent directory exceeds its entry limit.",
                        directory,
                    )
                if entry.name.lower() == "readme.md" or not entry.name.endswith(".md"):
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                if len(selected) >= _MAX_MARKDOWN_FILES:
                    raise _error(
                        "configuration_source_limit",
                        "Too many canonical subagent files were selected.",
                        directory,
                    )
                item = entry.stat(follow_symlinks=False)
                selected.append((Path(entry.path), item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns))
    except ConfigurationError:
        raise
    except OSError as exc:
        raise _error("settings_unavailable", "The canonical subagent directory cannot be listed.", directory) from exc
    return tuple(sorted(selected, key=lambda item: item[0].name))


def _parse_agent_ui_document(path: Path, content: bytes) -> AgentUiDocument:
    raw = _parse_yaml_mapping(
        path,
        content,
        code="settings_invalid",
        source_name="Agent UI configuration YAML",
    )
    _resolve_process_paths(raw, base=path.parent)
    try:
        serialized = json.dumps(raw, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        return AgentUiDocument.model_validate_json(serialized, strict=True)
    except (TypeError, ValueError, ValidationError) as exc:
        if isinstance(exc, ValidationError):
            raise _validation_error(
                "settings_invalid", "The Agent UI configuration document is invalid.", path, exc
            ) from exc
        raise _error("settings_invalid", "The Agent UI configuration document is invalid.", path) from exc


def _parse_canonical_markdown(path: Path, content: bytes) -> CanonicalSubagent:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(
            "configuration_markdown_invalid", "Canonical subagent Markdown must be valid UTF-8.", path
        ) from exc
    if "\x00" in text:
        raise _error("configuration_markdown_invalid", "Canonical subagent Markdown contains NUL.", path)
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0] != "---":
        raise _error("configuration_markdown_invalid", "Canonical subagent Markdown requires YAML frontmatter.", path)
    try:
        closing = lines.index("---", 1)
    except ValueError as exc:
        raise _error("configuration_markdown_invalid", "Canonical subagent frontmatter is not closed.", path) from exc
    frontmatter = "\n".join(lines[1:closing]).encode()
    raw = _parse_yaml_mapping(
        path,
        frontmatter,
        code="configuration_markdown_invalid",
        source_name="Canonical subagent frontmatter",
    )
    if "body" in raw:
        raise _error("configuration_markdown_invalid", "Markdown body is not a frontmatter field.", path)
    raw["body"] = "\n".join(lines[closing + 1 :]).strip()
    try:
        return CanonicalSubagent.model_validate(raw, strict=True)
    except ValidationError as exc:
        raise _validation_error(
            "configuration_markdown_invalid",
            "A canonical subagent definition is invalid.",
            path,
            exc,
        ) from exc


def _resolve_process_paths(raw: dict[str, Any], *, base: Path) -> None:
    process = raw.get("process")
    if not isinstance(process, dict):
        return
    storage = process.get("storage")
    if isinstance(storage, dict):
        _resolve_mapping_path(storage, "data_root", base)
    envd_runtime = process.get("envd_runtime")
    if isinstance(envd_runtime, dict):
        _resolve_mapping_path(envd_runtime, "executable", base)


def _resolve_mapping_path(mapping: dict[str, Any], name: str, base: Path) -> None:
    value = mapping.get(name)
    if not isinstance(value, str) or "\x00" in value:
        return
    expanded = Path(value).expanduser()
    if not expanded.is_absolute():
        expanded = base / expanded
    mapping[name] = str(expanded.resolve(strict=False))


def _parse_yaml_mapping(
    path: Path,
    content: bytes,
    *,
    code: str,
    source_name: str,
) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise _error(code, f"{source_name} is not valid UTF-8.", path) from exc
    if "\x00" in text:
        raise _error(code, f"{source_name} contains NUL.", path)
    try:
        depth = 0
        nodes = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None) is not None:
                raise _error(code, f"{source_name} forbids YAML anchors and aliases.", path)
            tag = getattr(event, "tag", None)
            if tag is not None and not str(tag).startswith("tag:yaml.org,2002:"):
                raise _error(code, f"{source_name} forbids custom YAML tags.", path)
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                depth += 1
                nodes += 1
            elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                depth -= 1
            elif isinstance(event, yaml.events.ScalarEvent):
                nodes += 1
            if nodes > _MAX_YAML_NODES or depth > _MAX_YAML_DEPTH:
                raise _error("settings_source_limit", "A configuration YAML source exceeds structural limits.", path)
        value = yaml.load(text, Loader=_UniqueSafeLoader)
    except ConfigurationError:
        raise
    except RecursionError as exc:
        raise _error("settings_source_limit", f"{source_name} exceeds structural limits.", path) from exc
    except (yaml.YAMLError, UnicodeError, ValueError) as exc:
        raise _error(code, f"{source_name} has invalid syntax.", path) from exc
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise _error(code, f"{source_name} must be a string-keyed mapping.", path)
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


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def _validation_error(code: str, message: str, path: Path, exc: ValidationError) -> ConfigurationError:
    first = exc.errors(include_input=False, include_url=False)[0] if exc.error_count() else None
    details: dict[str, Any] = {"path": str(path), "validation_error_count": exc.error_count()}
    if first is not None:
        details["location"] = ".".join(str(item) for item in first["loc"])
        details["reason"] = first["type"]
    return ConfigurationError(message, code=code, details=details)


def _error(code: str, message: str, path: Path) -> ConfigurationError:
    return ConfigurationError(message, code=code, details={"path": str(path)})


__all__ = ["empty_agent_ui_configuration", "load_agent_ui_configuration"]
