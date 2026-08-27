"""Verified immutable Zstandard object storage for Agent UI authority payloads."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated
from uuid import uuid4

import zstandard
from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator

from a13n_ui.errors import ObjectIntegrityError, StoreIntegrityError

from .layout import StorageLayout

if TYPE_CHECKING:
    from a13n_ui.settings import StorageSettings

_SCHEMA_VERSION = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_SUPPORTED_OBJECT_SCHEMA_VERSION = "1"
_SUPPORTED_PAYLOAD_CODEC_VERSION = "1"


class ObjectKind(StrEnum):
    """Immutable payload families owned by the Agent UI store."""

    agent_snapshot = "agent-snapshot"
    environment_snapshot = "environment-snapshot"
    resource_revision = "resource-revision"
    skill_package = "skill-package"
    harness_state = "harness-state"
    deferred_requests = "deferred-requests"
    provider_state = "provider-state"


class ObjectRef(BaseModel):
    """Authority-neutral identity for one verified immutable object."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    object_kind: ObjectKind
    object_schema_version: _SCHEMA_VERSION
    logical_digest: _DIGEST

    @field_validator("object_schema_version")
    @classmethod
    def _require_supported_schema(cls, value: str) -> str:
        if value != _SUPPORTED_OBJECT_SCHEMA_VERSION:
            raise ValueError("object_schema_version is not supported")
        return value


class ObjectEnvelope(BaseModel):
    """Self-describing canonical content stored inside one Zstandard frame."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    object_kind: ObjectKind
    object_schema_version: _SCHEMA_VERSION
    logical_digest: _DIGEST
    created_at: datetime
    producer_release: str = Field(min_length=1, max_length=128)
    payload_codec_version: _SCHEMA_VERSION
    payload: JsonValue

    @field_validator("object_schema_version")
    @classmethod
    def _require_supported_schema(cls, value: str) -> str:
        if value != _SUPPORTED_OBJECT_SCHEMA_VERSION:
            raise ValueError("object_schema_version is not supported")
        return value

    @field_validator("payload_codec_version")
    @classmethod
    def _require_supported_payload_codec(cls, value: str) -> str:
        if value != _SUPPORTED_PAYLOAD_CODEC_VERSION:
            raise ValueError("payload_codec_version is not supported")
        return value

    @field_validator("created_at")
    @classmethod
    def _require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a UTC offset")
        return value.astimezone(UTC)

    @property
    def ref(self) -> ObjectRef:
        return ObjectRef(
            object_kind=self.object_kind,
            object_schema_version=self.object_schema_version,
            logical_digest=self.logical_digest,
        )


class RecoveryDiagnostic(BaseModel):
    """Bounded safe result of staging reconciliation."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    code: str = Field(min_length=1, max_length=64)
    entry_name: str = Field(min_length=1, max_length=255)


class ImmutableObjectStore:
    """Publish and verify content-addressed Agent UI payload files."""

    def __init__(self, layout: StorageLayout, settings: StorageSettings, *, producer_release: str) -> None:
        self._layout = layout
        self._settings = settings
        self._producer_release = producer_release

    async def publish(
        self,
        *,
        object_kind: ObjectKind,
        object_schema_version: str,
        payload: JsonValue,
        payload_codec_version: str = "1",
        created_at: datetime | None = None,
    ) -> ObjectEnvelope:
        """Publish one verified object and return its detached envelope."""

        return await to_thread.run_sync(
            partial(
                self._publish,
                object_kind=object_kind,
                object_schema_version=object_schema_version,
                payload=payload,
                payload_codec_version=payload_codec_version,
                created_at=created_at,
            )
        )

    async def read(self, reference: ObjectRef) -> ObjectEnvelope:
        """Read and verify one exact object reference."""

        return await to_thread.run_sync(self._read_ref, reference)

    async def recover_staging(self) -> tuple[RecoveryDiagnostic, ...]:
        """Finish valid staged publications and quarantine malformed candidates."""

        return await to_thread.run_sync(self._recover_staging)

    async def remove(self, reference: ObjectRef) -> None:
        """Remove one unreferenced object selected by the metadata owner."""

        await to_thread.run_sync(self._remove_ref, reference)

    async def remove_expired_unregistered(
        self,
        registered_digests: set[str],
        *,
        cutoff: datetime,
    ) -> int:
        """Remove old final object files that were never registered in SQLite."""

        return await to_thread.run_sync(self._remove_expired_unregistered, registered_digests, cutoff)

    def _remove_ref(self, reference: ObjectRef) -> None:
        path = self._path_for(reference)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ObjectIntegrityError(
                "An expired immutable object could not be removed.",
                code="object_cleanup_failed",
            ) from exc
        if path.parent.exists():
            self._sync_directory(path.parent)

    def _remove_expired_unregistered(self, registered_digests: set[str], cutoff: datetime) -> int:
        removed = 0
        for path in self._layout.objects.glob("*/*/*/*.json.zst"):
            try:
                relative = path.relative_to(self._layout.objects)
                kind, version, prefix, filename = relative.parts
                digest = filename.removesuffix(".json.zst")
                reference = ObjectRef(
                    object_kind=ObjectKind(kind),
                    object_schema_version=version,
                    logical_digest=digest,
                )
                metadata = path.stat(follow_symlinks=False)
            except (OSError, ValueError):
                continue
            if (
                reference.logical_digest in registered_digests
                or prefix != reference.logical_digest[:2]
                or self._path_for(reference) != path
                or datetime.fromtimestamp(metadata.st_mtime, tz=UTC) >= cutoff
            ):
                continue
            self._remove_ref(reference)
            removed += 1
        return removed

    def _publish(
        self,
        *,
        object_kind: ObjectKind,
        object_schema_version: str,
        payload: JsonValue,
        payload_codec_version: str,
        created_at: datetime | None,
    ) -> ObjectEnvelope:
        try:
            envelope = ObjectEnvelope(
                object_kind=object_kind,
                object_schema_version=object_schema_version,
                logical_digest="0" * 64,
                created_at=created_at or datetime.now(UTC),
                producer_release=self._producer_release,
                payload_codec_version=payload_codec_version,
                payload=payload,
            )
        except ValidationError as exc:
            raise StoreIntegrityError(
                "Immutable object envelope input is invalid.",
                code="object_payload_invalid",
                details={"object_kind": object_kind.value},
            ) from exc
        digest_input = envelope.model_dump(mode="json", exclude={"logical_digest"})
        digest = hashlib.sha256(_canonical_json(digest_input)).hexdigest()
        envelope = envelope.model_copy(update={"logical_digest": digest})
        uncompressed = _canonical_json(envelope.model_dump(mode="json"))
        if len(uncompressed) > self._settings.max_object_bytes:
            raise StoreIntegrityError(
                "Immutable object exceeds the configured uncompressed size limit.",
                code="object_too_large",
                details={"object_kind": object_kind.value},
            )
        compressed = zstandard.ZstdCompressor(write_checksum=True).compress(uncompressed)
        stage = self._layout.staging / f"{uuid4().hex}.json.zst.tmp"
        try:
            self._write_stage(stage, compressed)
            staged = self._read_path(stage)
            if staged != envelope:
                raise ObjectIntegrityError(
                    "Staged immutable object did not verify to its source envelope.",
                    code="object_staging_mismatch",
                )
            self._publish_stage(stage, envelope.ref)
            return envelope
        finally:
            stage.unlink(missing_ok=True)

    def _write_stage(self, path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _publish_stage(self, stage: Path, reference: ObjectRef) -> None:
        target = self._path_for(reference)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.link(stage, target)
        except FileExistsError:
            existing = self._read_path(target)
            if existing.ref != reference:
                raise ObjectIntegrityError(
                    "An incompatible immutable object occupies a digest-derived path.",
                    code="object_digest_collision",
                    details={"logical_digest": reference.logical_digest},
                ) from None
        else:
            if os.name != "nt":
                target.chmod(0o600)
            self._sync_publication_directories(target.parent)
        published = self._read_path(target)
        if published.ref != reference:
            raise ObjectIntegrityError(
                "Published immutable object does not match its expected reference.",
                code="object_publication_mismatch",
                details={"logical_digest": reference.logical_digest},
            )

    def _read_ref(self, reference: ObjectRef) -> ObjectEnvelope:
        envelope = self._read_path(self._path_for(reference))
        if envelope.ref != reference:
            raise ObjectIntegrityError(
                "Immutable object identity does not match the selected reference.",
                code="object_reference_mismatch",
                details={"logical_digest": reference.logical_digest},
            )
        return envelope

    def _read_path(self, path: Path) -> ObjectEnvelope:
        try:
            compressed_size = path.stat().st_size
            if compressed_size > self._settings.max_object_bytes * 2:
                raise ObjectIntegrityError(
                    "Compressed immutable object exceeds its storage limit.",
                    code="object_compressed_too_large",
                )
            compressed = path.read_bytes()
            frame = zstandard.get_frame_parameters(compressed)
            if not frame.has_checksum:
                raise ObjectIntegrityError(
                    "Immutable object Zstandard frame has no checksum.",
                    code="object_checksum_missing",
                )
            if (
                frame.content_size not in {zstandard.CONTENTSIZE_UNKNOWN, zstandard.CONTENTSIZE_ERROR}
                and frame.content_size > self._settings.max_object_bytes
            ):
                raise ObjectIntegrityError(
                    "Immutable object exceeds the configured uncompressed size limit.",
                    code="object_too_large",
                )
            uncompressed = zstandard.ZstdDecompressor().decompress(
                compressed,
                max_output_size=self._settings.max_object_bytes + 1,
                allow_extra_data=False,
            )
        except ObjectIntegrityError:
            raise
        except (OSError, zstandard.ZstdError) as exc:
            raise ObjectIntegrityError(
                "Immutable object is missing, unreadable, or not a complete Zstandard frame.",
                code="object_unreadable",
            ) from exc
        if len(uncompressed) > self._settings.max_object_bytes:
            raise ObjectIntegrityError(
                "Immutable object exceeds the configured uncompressed size limit.",
                code="object_too_large",
            )
        try:
            envelope = ObjectEnvelope.model_validate_json(uncompressed, strict=True)
        except ValueError as exc:
            raise ObjectIntegrityError(
                "Immutable object envelope is malformed or incompatible.",
                code="object_envelope_invalid",
            ) from exc
        canonical = _canonical_json(envelope.model_dump(mode="json"))
        if canonical != uncompressed:
            raise ObjectIntegrityError(
                "Immutable object content is not in canonical form.",
                code="object_not_canonical",
            )
        digest_input = envelope.model_dump(mode="json", exclude={"logical_digest"})
        expected = hashlib.sha256(_canonical_json(digest_input)).hexdigest()
        if expected != envelope.logical_digest:
            raise ObjectIntegrityError(
                "Immutable object logical digest does not match its content.",
                code="object_digest_mismatch",
                details={"logical_digest": envelope.logical_digest},
            )
        return envelope

    def _recover_staging(self) -> tuple[RecoveryDiagnostic, ...]:
        entries = sorted(self._layout.staging.iterdir(), key=lambda path: path.name)
        if len(entries) > self._settings.max_staging_entries:
            raise StoreIntegrityError(
                "Agent UI staging contains too many entries to recover safely.",
                code="staging_too_large",
                details={"entry_count": len(entries)},
            )
        diagnostics: list[RecoveryDiagnostic] = []
        for entry in entries:
            try:
                metadata = entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise ObjectIntegrityError("Staging entry is not a regular file.", code="staging_entry_invalid")
                envelope = self._read_path(entry)
                self._publish_stage(entry, envelope.ref)
            except (OSError, ObjectIntegrityError):
                destination = self._layout.quarantine / f"{uuid4().hex}-{entry.name[:120]}"
                os.replace(entry, destination)
                diagnostics.append(RecoveryDiagnostic(code="staging_quarantined", entry_name=entry.name[:255]))
            else:
                entry.unlink(missing_ok=True)
                diagnostics.append(RecoveryDiagnostic(code="staging_recovered", entry_name=entry.name[:255]))
        if entries:
            self._sync_directory(self._layout.staging)
            self._sync_directory(self._layout.quarantine)
        return tuple(diagnostics)

    def _path_for(self, reference: ObjectRef) -> Path:
        return (
            self._layout.objects
            / reference.object_kind.value
            / reference.object_schema_version
            / reference.logical_digest[:2]
            / f"{reference.logical_digest}.json.zst"
        )

    def _sync_publication_directories(self, leaf: Path) -> None:
        current = leaf
        while True:
            self._sync_directory(current)
            if current == self._layout.objects:
                return
            current = current.parent

    def _sync_directory(self, path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StoreIntegrityError(
            "Immutable object payload is not finite canonical JSON.",
            code="object_payload_invalid",
        ) from exc


__all__ = [
    "ImmutableObjectStore",
    "ObjectEnvelope",
    "ObjectKind",
    "ObjectRef",
    "RecoveryDiagnostic",
]
