"""Application-owned optimistic source edits with one atomic active manifest."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from uuid import uuid4

from anyio import to_thread
from pydantic import ValidationError

from a13n_ui.errors import ConfigurationError

from .models import (
    ConfigurationSettings,
    SourceTransactionEntry,
    SourceTransactionManifest,
)

_TRANSACTION_DIRECTORY = ".a13n-transactions"
_ACTIVE_MANIFEST = "active.json"
_VALUES_DIRECTORY = "values"


@dataclass(frozen=True, slots=True)
class PreparedSourceTransaction:
    """A durable complete overlay that is not yet selected as desired source state."""

    root: Path
    manifest: SourceTransactionManifest
    overlay: Mapping[str, bytes | None]
    previous_manifest: SourceTransactionManifest | None


async def prepare_source_transaction(
    settings: ConfigurationSettings,
    manifest: SourceTransactionManifest,
    replacements: Mapping[str, bytes],
    *,
    current_catalog_digest: str,
) -> PreparedSourceTransaction:
    """Stage and verify a complete overlay without changing the active manifest."""

    if manifest.base_catalog_digest != current_catalog_digest:
        raise ConfigurationError(
            "The source transaction is based on a stale catalog generation.",
            code="source_transaction_stale",
        )
    roots = {root.root_id: root for root in settings.ordered_roots}
    root = roots.get(manifest.root_id)
    if root is None or not root.writable:
        raise ConfigurationError(
            "The source transaction root is not writable by Agent UI.",
            code="source_transaction_denied",
        )
    expected = {entry.relative_path for entry in manifest.entries if entry.operation == "replace"}
    if set(replacements) != expected:
        raise ConfigurationError(
            "Source transaction replacement values are missing or unexpected.",
            code="source_transaction_incomplete",
        )
    detached: dict[str, bytes] = {}
    total_bytes = 0
    managed_skill_bytes = 0
    for entry in manifest.entries:
        if entry.operation != "replace":
            continue
        content = bytes(replacements[entry.relative_path])
        total_bytes += len(content)
        if total_bytes > settings.max_source_transaction_bytes:
            raise ConfigurationError(
                "A source transaction exceeds its total size limit.",
                code="source_transaction_limit",
            )
        parts = PurePosixPath(entry.relative_path).parts
        if parts and parts[0] == "managed-skills":
            managed_skill_bytes += len(content)
            if managed_skill_bytes > settings.max_skill_package_bytes:
                raise ConfigurationError(
                    "A managed Skill package transaction exceeds its size limit.",
                    code="skill_package_limit",
                )
        elif len(content) > settings.max_source_bytes:
            raise ConfigurationError(
                "A source transaction value exceeds its size limit.",
                code="configuration_source_limit",
            )
        if hashlib.sha256(content).hexdigest() != entry.content_digest:
            raise ConfigurationError(
                "A source transaction replacement digest does not match.",
                code="source_transaction_digest_mismatch",
            )
        detached[entry.relative_path] = content
    return await to_thread.run_sync(partial(_prepare_transaction, root.path, manifest, detached))


async def commit_source_transaction(prepared: PreparedSourceTransaction) -> None:
    """Atomically select a previously verified overlay."""

    await to_thread.run_sync(_commit_transaction, prepared)


async def finalize_source_transaction(
    prepared: PreparedSourceTransaction,
    *,
    accepted_catalog_digest: str,
) -> None:
    """Rebase the selected manifest and remove its superseded overlay."""

    await to_thread.run_sync(
        partial(
            _finalize_transaction,
            prepared,
            accepted_catalog_digest=accepted_catalog_digest,
        )
    )


async def rollback_source_transaction(prepared: PreparedSourceTransaction) -> None:
    """Restore the previously selected overlay after post-selection rejection."""

    await to_thread.run_sync(_rollback_transaction, prepared)


async def discard_source_transaction(prepared: PreparedSourceTransaction) -> None:
    """Remove an unselected prepared overlay after candidate rejection."""

    await to_thread.run_sync(_discard_transaction, prepared)


async def rebase_source_transaction(
    root: Path,
    manifest: SourceTransactionManifest,
    *,
    accepted_catalog_digest: str,
) -> None:
    """Record the generation that most recently accepted one active overlay."""

    await to_thread.run_sync(
        partial(
            _rebase_active_manifest,
            root,
            manifest,
            accepted_catalog_digest=accepted_catalog_digest,
        )
    )


def read_source_overlay(root: Path) -> tuple[SourceTransactionManifest | None, dict[str, bytes | None]]:
    """Read and verify the one active source overlay selected for a root."""

    transaction_root = root / _TRANSACTION_DIRECTORY
    active = transaction_root / _ACTIVE_MANIFEST
    if not active.exists():
        return None, {}
    manifest = _read_manifest(active)
    return manifest, _read_transaction_values(transaction_root, manifest)


def _prepare_transaction(
    root: Path,
    requested: SourceTransactionManifest,
    replacements: Mapping[str, bytes],
) -> PreparedSourceTransaction:
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    transaction_root = root / _TRANSACTION_DIRECTORY
    transaction_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous_manifest, previous = read_source_overlay(root)
    merged = dict(previous)
    for entry in requested.entries:
        merged[entry.relative_path] = replacements[entry.relative_path] if entry.operation == "replace" else None

    destination = transaction_root / requested.transaction_id
    if destination.exists():
        raise ConfigurationError(
            "The source transaction ID is already present.",
            code="source_transaction_conflict",
        )
    temporary = transaction_root / f".{requested.transaction_id}-{uuid4().hex}.tmp"
    values_root = temporary / _VALUES_DIRECTORY
    values_root.mkdir(mode=0o700, parents=True)
    entries: list[SourceTransactionEntry] = []
    try:
        for relative, content in sorted(merged.items()):
            if content is None:
                entries.append(SourceTransactionEntry(relative_path=relative, operation="delete"))
                continue
            staged = values_root.joinpath(*PurePosixPath(relative).parts)
            staged.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _write_file(staged, content)
            entries.append(
                SourceTransactionEntry(
                    relative_path=relative,
                    operation="replace",
                    content_digest=hashlib.sha256(content).hexdigest(),
                )
            )
        active_manifest = SourceTransactionManifest(
            schema_version="1",
            transaction_id=requested.transaction_id,
            root_id=requested.root_id,
            base_catalog_digest=requested.base_catalog_digest,
            entries=tuple(entries),
        )
        _sync_tree(temporary)
        os.replace(temporary, destination)
        _sync_directory(transaction_root)
        overlay = _read_transaction_values(transaction_root, active_manifest)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return PreparedSourceTransaction(
        root=root,
        manifest=active_manifest,
        overlay=MappingProxyType(overlay),
        previous_manifest=previous_manifest,
    )


def _commit_transaction(prepared: PreparedSourceTransaction) -> None:
    transaction_root = prepared.root / _TRANSACTION_DIRECTORY
    verified = _read_transaction_values(transaction_root, prepared.manifest)
    if verified != prepared.overlay:
        raise ConfigurationError(
            "The prepared source transaction changed before commit.",
            code="source_transaction_incomplete",
        )
    _replace_active_manifest(transaction_root, prepared.manifest)


def _finalize_transaction(
    prepared: PreparedSourceTransaction,
    *,
    accepted_catalog_digest: str,
) -> None:
    transaction_root = prepared.root / _TRANSACTION_DIRECTORY
    _rebase_active_manifest(
        prepared.root,
        prepared.manifest,
        accepted_catalog_digest=accepted_catalog_digest,
    )
    previous = prepared.previous_manifest
    if previous is not None and previous.transaction_id != prepared.manifest.transaction_id:
        shutil.rmtree(transaction_root / previous.transaction_id, ignore_errors=True)


def _rollback_transaction(prepared: PreparedSourceTransaction) -> None:
    transaction_root = prepared.root / _TRANSACTION_DIRECTORY
    active = transaction_root / _ACTIVE_MANIFEST
    selected = _read_manifest(active)
    if selected.transaction_id != prepared.manifest.transaction_id:
        raise ConfigurationError(
            "The selected source transaction changed before rollback.",
            code="source_transaction_rollback_failed",
        )
    previous = prepared.previous_manifest
    if previous is None:
        active.unlink()
        _sync_directory(transaction_root)
    else:
        _read_transaction_values(transaction_root, previous)
        _replace_active_manifest(transaction_root, previous)
    shutil.rmtree(transaction_root / prepared.manifest.transaction_id, ignore_errors=True)


def _rebase_active_manifest(
    root: Path,
    manifest: SourceTransactionManifest,
    *,
    accepted_catalog_digest: str,
) -> None:
    transaction_root = root / _TRANSACTION_DIRECTORY
    selected = _read_manifest(transaction_root / _ACTIVE_MANIFEST)
    if selected.transaction_id != manifest.transaction_id:
        raise ConfigurationError(
            "The active source transaction changed before it could be finalized.",
            code="source_transaction_stale",
        )
    rebased = selected.model_copy(update={"base_catalog_digest": accepted_catalog_digest})
    _replace_active_manifest(transaction_root, rebased)


def _replace_active_manifest(
    transaction_root: Path,
    manifest: SourceTransactionManifest,
) -> None:
    staged_manifest = transaction_root / f".{_ACTIVE_MANIFEST}-{uuid4().hex}.tmp"
    try:
        _write_file(staged_manifest, manifest.model_dump_json().encode("utf-8"))
        os.replace(staged_manifest, transaction_root / _ACTIVE_MANIFEST)
        _sync_directory(transaction_root)
    except OSError as exc:
        staged_manifest.unlink(missing_ok=True)
        raise ConfigurationError(
            "The source transaction manifest could not be selected.",
            code="source_transaction_commit_failed",
        ) from exc


def _discard_transaction(prepared: PreparedSourceTransaction) -> None:
    transaction_root = prepared.root / _TRANSACTION_DIRECTORY
    active = transaction_root / _ACTIVE_MANIFEST
    if active.exists():
        try:
            selected = _read_manifest(active)
        except ConfigurationError:
            return
        if selected.transaction_id == prepared.manifest.transaction_id:
            return
    shutil.rmtree(transaction_root / prepared.manifest.transaction_id, ignore_errors=True)


def _read_manifest(path: Path) -> SourceTransactionManifest:
    try:
        raw = json.loads(path.read_bytes(), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
        return SourceTransactionManifest.model_validate(raw, strict=True)
    except (OSError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        raise ConfigurationError(
            "The active source transaction manifest is malformed.",
            code="source_transaction_invalid",
        ) from exc


def _read_transaction_values(
    transaction_root: Path,
    manifest: SourceTransactionManifest,
) -> dict[str, bytes | None]:
    values_root = transaction_root / manifest.transaction_id / _VALUES_DIRECTORY
    replacements = {entry.relative_path for entry in manifest.entries if entry.operation == "replace"}
    actual = _regular_relative_files(values_root) if values_root.exists() else set()
    if actual != replacements:
        raise ConfigurationError(
            "The source transaction has missing or unexpected staged values.",
            code="source_transaction_incomplete",
        )
    overlay: dict[str, bytes | None] = {}
    for entry in manifest.entries:
        if entry.operation == "delete":
            overlay[entry.relative_path] = None
            continue
        path = values_root.joinpath(*PurePosixPath(entry.relative_path).parts)
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise ConfigurationError(
                "A source transaction value cannot be read.",
                code="source_transaction_incomplete",
            ) from exc
        if hashlib.sha256(content).hexdigest() != entry.content_digest:
            raise ConfigurationError(
                "A source transaction value has the wrong digest.",
                code="source_transaction_digest_mismatch",
            )
        overlay[entry.relative_path] = content
    return overlay


def _regular_relative_files(root: Path) -> set[str]:
    files: set[str] = set()
    for current, directories, names in os.walk(root, followlinks=False):
        directories.sort()
        names.sort()
        current_path = Path(current)
        for directory in directories:
            if (current_path / directory).is_symlink():
                raise ConfigurationError(
                    "Source transaction value directories cannot be symlinks.",
                    code="source_transaction_invalid",
                )
        for name in names:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise ConfigurationError(
                    "Source transaction values must be regular files.",
                    code="source_transaction_invalid",
                )
            files.add(path.relative_to(root).as_posix())
    return files


def _write_file(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_tree(root: Path) -> None:
    if os.name == "nt":
        return
    directories = [root]
    for current, nested, _files in os.walk(root):
        directories.extend(Path(current) / name for name in nested)
    for path in reversed(directories):
        _sync_directory(path)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate source transaction manifest key")
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite source transaction value: {value}")


__all__ = [
    "PreparedSourceTransaction",
    "commit_source_transaction",
    "discard_source_transaction",
    "prepare_source_transaction",
    "read_source_overlay",
]
