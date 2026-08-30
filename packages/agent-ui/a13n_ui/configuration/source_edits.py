"""Simple last-write-wins edits for writable configuration roots."""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import partial
from pathlib import Path, PurePosixPath
from uuid import uuid4

from anyio import to_thread

from a13n_ui.errors import ConfigurationError

from .models import ConfigurationSettings


async def apply_source_edits(
    settings: ConfigurationSettings,
    root_id: str,
    edits: Mapping[str, bytes | None],
) -> None:
    """Apply validated files independently with ordinary atomic replacement."""

    root = _writable_root(settings, root_id)
    detached = _validate_edits(settings, edits)
    await to_thread.run_sync(partial(_apply_edits, root, detached))


def _writable_root(settings: ConfigurationSettings, root_id: str) -> Path:
    selected = next((item for item in settings.ordered_roots if item.root_id == root_id), None)
    if selected is None or not selected.writable:
        raise ConfigurationError(
            "The selected definition root is not writable by Agent UI.",
            code="source_edit_denied",
        )
    return selected.path


def _validate_edits(
    settings: ConfigurationSettings,
    edits: Mapping[str, bytes | None],
) -> dict[str, bytes | None]:
    if not edits:
        raise ConfigurationError("A source edit must change at least one file.", code="source_edit_empty")
    if len(edits) > settings.max_source_files:
        raise ConfigurationError("A source edit changes too many files.", code="configuration_source_limit")
    detached: dict[str, bytes | None] = {}
    skill_bytes = 0
    skill_files = 0
    for relative_path, selected in edits.items():
        normalized = _relative_path(relative_path)
        if normalized in detached:
            raise ConfigurationError("A source edit repeats a path.", code="source_edit_invalid")
        if selected is None:
            detached[normalized] = None
            continue
        content = bytes(selected)
        parts = PurePosixPath(normalized).parts
        if parts and parts[0] == "managed-skills":
            skill_files += 1
            skill_bytes += len(content)
            if skill_files > settings.max_skill_package_files or skill_bytes > settings.max_skill_package_bytes:
                raise ConfigurationError("A managed Skill package exceeds its limits.", code="skill_package_limit")
        elif len(content) > settings.max_source_bytes:
            raise ConfigurationError(
                "A configuration source exceeds its size limit.", code="configuration_source_limit"
            )
        detached[normalized] = content
    return detached


def _relative_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ConfigurationError("A source edit path is invalid.", code="source_edit_invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ConfigurationError("A source edit path is invalid.", code="source_edit_invalid")
    normalized = path.as_posix()
    if len(normalized) > 1024:
        raise ConfigurationError("A source edit path is too long.", code="source_edit_invalid")
    return normalized


def _apply_edits(root: Path, edits: Mapping[str, bytes | None]) -> None:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    replacements = tuple(sorted((path, content) for path, content in edits.items() if content is not None))
    deletions = tuple(sorted(path for path, content in edits.items() if content is None))
    try:
        for relative_path, content in replacements:
            destination = _destination(root, relative_path)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            temporary = destination.with_name(f".{destination.name}-{uuid4().hex}.tmp")
            try:
                temporary.write_bytes(content)
                os.replace(temporary, destination)
            finally:
                temporary.unlink(missing_ok=True)
        for relative_path in deletions:
            _destination(root, relative_path).unlink(missing_ok=True)
    except OSError as exc:
        raise ConfigurationError("Configuration source files could not be written.", code="source_edit_failed") from exc


def _destination(root: Path, relative_path: str) -> Path:
    destination = root.joinpath(*PurePosixPath(relative_path).parts)
    resolved_root = root.resolve(strict=False)
    resolved_parent = destination.parent.resolve(strict=False)
    if resolved_parent != resolved_root and resolved_root not in resolved_parent.parents:
        raise ConfigurationError("A source edit escapes its definition root.", code="source_edit_invalid")
    return destination


__all__ = ["apply_source_edits"]
