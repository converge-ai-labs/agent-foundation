"""Verified immutable Zstandard object storage for Harness UI authority payloads."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, TypeVar
from uuid import uuid4

import zstandard
from a13n_logging import get_logger
from anyio import to_thread
from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, ValidationError, field_validator

from a13n_harness_ui.errors import ObjectIntegrityError, StoreIntegrityError

from .layout import StorageLayout

if TYPE_CHECKING:
    from a13n_harness_ui.settings import StorageSettings

_SCHEMA_VERSION = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")]
_DIGEST = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_SUPPORTED_OBJECT_SCHEMA_VERSION = "1"
_SUPPORTED_PAYLOAD_CODEC_VERSIONS = frozenset({"1", "2"})
_ObjectModelT = TypeVar("_ObjectModelT", bound=BaseModel)
_PAYLOAD_ADAPTER = TypeAdapter(JsonValue)
_LOGGER = get_logger(__name__)


def _validation_diagnostics(error: ValidationError, model_type: type[BaseModel]) -> dict[str, object]:
    # Locations can contain arbitrary dictionary keys (including paths or secrets).
    # Only schema-owned field names and numeric positions belong in diagnostics.
    fields = set(model_type.model_fields)
    try:
        schema = model_type.model_json_schema()
    except (TypeError, ValueError):
        schema = {}
    pending = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            properties = node.get("properties")
            if isinstance(properties, dict):
                fields.update(properties)
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    issues = error.errors(include_input=False, include_context=False, include_url=False)
    return {
        "validation_error_count": error.error_count(),
        "validation_errors": [
            {
                "location": ".".join(
                    str(part) if isinstance(part, int) or part in fields else "*" for part in issue["loc"][:16]
                ),
                "type": issue["type"],
            }
            for issue in issues[:8]
        ],
    }


class ObjectKind(StrEnum):
    """Immutable payload families owned by the Harness UI store."""

    configuration_generation = "configuration-generation"
    run_composition = "run-composition"
    thread_initial_state = "thread-initial-state"
    continuation = "continuation"
    child_checkpoint = "child-checkpoint"
    environment_state = "environment-state"
    mcp_app_resource = "mcp-app-resource"
    mcp_app_snapshot = "mcp-app-snapshot"


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
        if value not in _SUPPORTED_PAYLOAD_CODEC_VERSIONS:
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


class ImmutableObjectStore:
    """Publish and verify content-addressed Harness UI payload files."""

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
        payload_codec_version: str | None = None,
        created_at: datetime | None = None,
    ) -> ObjectEnvelope:
        """Publish one verified object and return its detached envelope."""

        return await to_thread.run_sync(
            partial(
                self._publish,
                object_kind=object_kind,
                object_schema_version=object_schema_version,
                payload=payload,
                payload_codec_version=(
                    payload_codec_version
                    if payload_codec_version is not None
                    else "2"
                    if object_kind
                    in {
                        ObjectKind.thread_initial_state,
                        ObjectKind.continuation,
                        ObjectKind.child_checkpoint,
                    }
                    else "1"
                ),
                created_at=created_at,
            )
        )

    async def read(self, reference: ObjectRef) -> ObjectEnvelope:
        """Read and verify one exact object reference."""

        return await to_thread.run_sync(self._read_ref, reference)

    async def publish_model(
        self,
        *,
        object_kind: ObjectKind,
        value: BaseModel,
        object_schema_version: str = "1",
        payload_codec_version: str | None = None,
    ) -> ObjectEnvelope:
        """Validate and publish one typed immutable payload."""

        payload = await to_thread.run_sync(partial(value.model_dump, mode="json"))
        return await self.publish(
            object_kind=object_kind,
            object_schema_version=object_schema_version,
            payload=payload,
            payload_codec_version=payload_codec_version,
        )

    async def read_model(
        self,
        reference: ObjectRef,
        model_type: type[_ObjectModelT],
    ) -> _ObjectModelT:
        """Read an exact object and validate its typed payload contract."""

        return await to_thread.run_sync(self._read_model, reference, model_type)

    def _read_model(self, reference: ObjectRef, model_type: type[_ObjectModelT]) -> _ObjectModelT:
        envelope = self._read_ref(reference)
        try:
            # This transient encoding is not an object identity. The complete
            # envelope has already passed canonical, finite-JSON and digest checks.
            serialized = _PAYLOAD_ADAPTER.dump_json(envelope.payload)
            # Snapshots outlive their writer's schema. Retain unknown fields as
            # opaque data, including configuration extras used in content digests.
            return model_type.model_validate_json(serialized, strict=True, extra="allow")
        except (TypeError, ValueError) as exc:
            details: dict[str, object] = {
                "object_kind": reference.object_kind.value,
                "object_schema_version": reference.object_schema_version,
                "object_digest": reference.logical_digest,
                "payload_codec_version": envelope.payload_codec_version,
                "payload_model": model_type.__name__,
            }
            if isinstance(exc, ValidationError):
                details.update(_validation_diagnostics(exc, model_type))
            else:
                details["validation_error_type"] = type(exc).__name__
            _LOGGER.warning("Immutable object payload is incompatible", extra=details)
            raise ObjectIntegrityError(
                "Immutable object payload does not match its expected contract.",
                code="object_payload_incompatible",
                details=details,
            ) from exc

    async def remove(self, reference: ObjectRef) -> None:
        """Remove an object whose owner has released it; callers coordinate reference use."""
        await to_thread.run_sync(partial(self._path_for(reference).unlink, missing_ok=True))

    async def references(self) -> tuple[ObjectRef, ...]:
        """List object files currently present in the local store."""

        return await to_thread.run_sync(self._references)

    def _references(self) -> tuple[ObjectRef, ...]:
        references: list[ObjectRef] = []
        for path in self._layout.objects.glob("*/*/*/*.json.zst"):
            try:
                kind, version, prefix, filename = path.relative_to(self._layout.objects).parts
                reference = ObjectRef(
                    object_kind=ObjectKind(kind),
                    object_schema_version=version,
                    logical_digest=filename.removesuffix(".json.zst"),
                )
            except ValueError:
                continue
            if prefix == reference.logical_digest[:2] and self._path_for(reference) == path:
                references.append(reference)
        return tuple(references)

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
        fields = _encoded_fields(envelope)
        digest = hashlib.sha256(_canonical_fields(fields, identity=True)).hexdigest()
        envelope = envelope.model_copy(update={"logical_digest": digest})
        fields["logical_digest"] = _canonical_json(digest)
        target = self._path_for(envelope.ref)
        if target.exists():
            return self._read_ref(envelope.ref)
        uncompressed = _canonical_fields(fields)
        if len(uncompressed) > self._settings.max_object_bytes:
            raise StoreIntegrityError(
                f"Immutable object requires {len(uncompressed)} uncompressed bytes; "
                f"the configured limit is {self._settings.max_object_bytes} bytes. "
                "Increase process.max_object_bytes and restart the application before retrying.",
                code="object_too_large",
                details={
                    "object_kind": object_kind.value,
                    "actual_bytes": len(uncompressed),
                    "max_object_bytes": self._settings.max_object_bytes,
                },
            )
        compressed = zstandard.ZstdCompressor(level=1, write_checksum=True).compress(uncompressed)
        stage = self._layout.staging / f"{uuid4().hex}.json.zst.tmp"
        try:
            self._write_stage(stage, compressed)
            staged = self._read_path(stage)
            if staged != envelope:
                raise ObjectIntegrityError(
                    "Staged immutable object did not verify to its source envelope.",
                    code="object_staging_mismatch",
                )
            return self._publish_stage(stage, envelope)
        finally:
            stage.unlink(missing_ok=True)

    def _write_stage(self, path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(content)
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    def _publish_stage(self, stage: Path, envelope: ObjectEnvelope) -> ObjectEnvelope:
        reference = envelope.ref
        target = self._path_for(reference)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.link(stage, target)
        except FileExistsError:
            published = self._read_ref(reference)
        else:
            if os.name != "nt":
                target.chmod(0o600)
            published = self._read_path(target)
        if published.ref != reference:
            raise ObjectIntegrityError(
                "Published immutable object does not match its expected reference.",
                code="object_publication_mismatch",
                details={"logical_digest": reference.logical_digest},
            )
        return published

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
                    f"Immutable object requires {frame.content_size} uncompressed bytes; "
                    f"the configured limit is {self._settings.max_object_bytes} bytes. "
                    "Increase process.max_object_bytes and restart the application before retrying.",
                    code="object_too_large",
                    details={
                        "actual_bytes": frame.content_size,
                        "max_object_bytes": self._settings.max_object_bytes,
                    },
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
                f"Immutable object requires {len(uncompressed)} uncompressed bytes; "
                f"the configured limit is {self._settings.max_object_bytes} bytes. "
                "Increase process.max_object_bytes and restart the application before retrying.",
                code="object_too_large",
                details={
                    "actual_bytes": len(uncompressed),
                    "max_object_bytes": self._settings.max_object_bytes,
                },
            )
        try:
            envelope = ObjectEnvelope.model_validate_json(uncompressed, strict=True)
        except ValueError as exc:
            raise ObjectIntegrityError(
                "Immutable object envelope is malformed or incompatible.",
                code="object_envelope_invalid",
            ) from exc
        fields = _encoded_fields(envelope)
        canonical = _canonical_fields(fields)
        if canonical != uncompressed:
            raise ObjectIntegrityError(
                "Immutable object content is not in canonical form.",
                code="object_not_canonical",
            )
        expected = hashlib.sha256(_canonical_fields(fields, identity=True)).hexdigest()
        if expected != envelope.logical_digest:
            raise ObjectIntegrityError(
                "Immutable object logical digest does not match its content.",
                code="object_digest_mismatch",
                details={"logical_digest": envelope.logical_digest},
            )
        return envelope

    def _path_for(self, reference: ObjectRef) -> Path:
        return (
            self._layout.objects
            / reference.object_kind.value
            / reference.object_schema_version
            / reference.logical_digest[:2]
            / f"{reference.logical_digest}.json.zst"
        )


def _encoded_fields(envelope: ObjectEnvelope) -> dict[str, bytes]:
    """Encode each value once for both the full envelope and its logical identity."""
    return {
        key: _canonical_json(value, sort_keys=key != "payload" or envelope.payload_codec_version == "1")
        for key, value in envelope.model_dump(mode="json").items()
    }


def _canonical_fields(fields: dict[str, bytes], *, identity: bool = False) -> bytes:
    # Codec 2 retains payload mapping order: providers render structured tool
    # results as text, so sorting a checkpoint would change the resumed prompt.
    # Envelope keys remain sorted under both codecs.
    # Only top-level publication metadata is excluded from logical identity.
    parts = [b"{"]
    for key in sorted(fields):
        if identity and key in {"logical_digest", "created_at"}:
            continue
        if len(parts) > 1:
            parts.append(b",")
        parts.extend((_canonical_json(key), b":", fields[key]))
    parts.append(b"}")
    return b"".join(parts)


def _canonical_json(value: object, *, sort_keys: bool = True) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=sort_keys,
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
]
