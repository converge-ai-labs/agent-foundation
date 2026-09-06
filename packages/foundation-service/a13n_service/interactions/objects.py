"""Object-store authority for complete Run state and oversized payloads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from a13n_logging import get_logger
from pydantic import TypeAdapter

from a13n_service.storage import ObjectConflict, ObjectInfo, ObjectNotFound, ObjectStore, ObjectStoreUnavailable
from a13n_service.storage.codec import DurableObjectCodecError, canonical_model_bytes, decode_canonical_model

from .domain import RunPayloadObjectRef
from .state import RunPayloadEnvelope, RunStateEnvelope, validate_state_successor

RUN_STATE_CONTENT_TYPE = "application/vnd.converge.run-state+json"
RUN_PAYLOAD_CONTENT_TYPE = "application/vnd.converge.run-payload+json"
DEFAULT_MAX_STATE_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_PAYLOAD_BYTES = 256 * 1024 * 1024

logger = get_logger(__name__)

_STATE_ADAPTER = TypeAdapter(RunStateEnvelope)
_PAYLOAD_ADAPTER = TypeAdapter(RunPayloadEnvelope)


class RunObjectError(RuntimeError):
    """A Run-owned object is invalid or violates the persistence contract."""


class RunObjectIntegrityError(RunObjectError):
    """Stored bytes or metadata do not match the selected Run object."""


class StaleStateWriter(RunObjectError):
    """A stale Attempt or object version tried to replace Run state."""


@dataclass(frozen=True, slots=True)
class StoredRunState:
    envelope: RunStateEnvelope
    info: ObjectInfo
    digest_sha256: str
    body: bytes

    @property
    def writer_fence(self) -> int:
        return self.envelope.writer_fence


class RunStateStore:
    def __init__(self, objects: ObjectStore, *, max_state_bytes: int = DEFAULT_MAX_STATE_BYTES) -> None:
        if max_state_bytes < 1:
            raise ValueError("max_state_bytes must be positive")
        self._objects = objects
        self._max_state_bytes = max_state_bytes

    async def create(self, organization_id: str, envelope: RunStateEnvelope) -> StoredRunState:
        if envelope.checkpoint_kind != "initial" or envelope.writer_fence != 0:
            raise ValueError("Run state creation requires an initial envelope with writer fence zero")
        body = canonical_model_bytes(envelope)
        self._require_bounded(body)
        digest = hashlib.sha256(body).hexdigest()
        key = run_state_key(organization_id, envelope.run_id)
        try:
            info = await self._put_state(
                key,
                body,
                envelope=envelope,
                digest=digest,
                if_none_match=True,
            )
        except ObjectConflict as error:
            raise StaleStateWriter("Run state already exists") from error
        _verify_info(info, key=key, body=body, content_type=RUN_STATE_CONTENT_TYPE)
        _verify_state_metadata(info, envelope=envelope, digest=digest)
        return StoredRunState(envelope, info, digest, body)

    async def read(
        self,
        organization_id: str,
        run_id: str,
        *,
        expected_thread_id: str | None = None,
    ) -> StoredRunState:
        key = run_state_key(organization_id, run_id)
        body, info = await _read_object(self._objects, key, max_bytes=self._max_state_bytes)
        _verify_info(info, key=key, body=body, content_type=RUN_STATE_CONTENT_TYPE)
        try:
            envelope = decode_canonical_model(body, _STATE_ADAPTER)
        except DurableObjectCodecError as error:
            raise RunObjectIntegrityError("Run state body is invalid") from error
        digest = hashlib.sha256(body).hexdigest()
        _verify_state_metadata(info, envelope=envelope, digest=digest)
        if envelope.run_id != run_id:
            raise RunObjectIntegrityError("Run state identity does not match its deterministic key")
        if expected_thread_id is not None and envelope.thread_id != expected_thread_id:
            raise RunObjectIntegrityError("Run state Thread identity does not match relational authority")
        return StoredRunState(envelope, info, digest, body)

    async def claim_writer(self, state: StoredRunState, *, fence: int) -> StoredRunState:
        """Fence prior writers without inventing a semantic checkpoint or rewriting its provenance."""

        if fence < 1:
            raise ValueError("Attempt writer fence must be positive")
        if fence < state.writer_fence:
            raise StaleStateWriter("Attempt fence is older than the state writer fence")
        envelope = state.envelope.model_copy(update={"writer_fence": fence})
        body = canonical_model_bytes(envelope)
        self._require_bounded(body)
        digest = hashlib.sha256(body).hexdigest()
        try:
            info = await self._put_state(
                state.info.key,
                body,
                envelope=envelope,
                digest=digest,
                if_match=state.info.version,
            )
        except ObjectConflict as error:
            raise StaleStateWriter("Run state changed before writer claim committed") from error
        _verify_info(info, key=state.info.key, body=body, content_type=RUN_STATE_CONTENT_TYPE)
        _verify_state_metadata(info, envelope=envelope, digest=digest)
        return StoredRunState(envelope, info, digest, body)

    async def replace(
        self,
        state: StoredRunState,
        successor: RunStateEnvelope,
        *,
        run_attempt_id: str,
        fence: int,
    ) -> StoredRunState:
        if fence != state.writer_fence:
            raise StaleStateWriter("Attempt must claim the current state writer fence before checkpoint publication")
        validate_state_successor(
            state.envelope,
            successor,
            run_attempt_id=run_attempt_id,
            fence=fence,
        )
        return await self._replace(state, successor)

    async def resume_completed(
        self,
        state: StoredRunState,
        *,
        run_attempt_id: str,
        fence: int,
    ) -> StoredRunState:
        """Publish only the recovery transition authorized by the Attempt service."""

        previous = state.envelope
        if (
            previous.checkpoint_kind != "completed"
            or fence != state.writer_fence
            or fence <= previous.last_checkpoint_fence
            or run_attempt_id == previous.last_checkpoint_run_attempt_id
        ):
            raise ValueError("completed recovery requires a claimed replacement Attempt")
        payload = previous.model_dump(mode="python", by_alias=True)
        payload.update(
            checkpoint_kind="progress",
            checkpoint_seq=previous.checkpoint_seq + 1,
            last_checkpoint_run_attempt_id=run_attempt_id,
            last_checkpoint_fence=fence,
            outcome_candidate=None,
        )
        successor = RunStateEnvelope.model_validate(payload)
        return await self._replace(state, successor)

    async def _replace(self, state: StoredRunState, successor: RunStateEnvelope) -> StoredRunState:
        body = canonical_model_bytes(successor)
        self._require_bounded(body)
        digest = hashlib.sha256(body).hexdigest()
        try:
            info = await self._put_state(
                state.info.key,
                body,
                envelope=successor,
                digest=digest,
                if_match=state.info.version,
            )
        except ObjectConflict as error:
            raise StaleStateWriter("Run state changed before the checkpoint committed") from error
        _verify_info(info, key=state.info.key, body=body, content_type=RUN_STATE_CONTENT_TYPE)
        _verify_state_metadata(info, envelope=successor, digest=digest)
        return StoredRunState(successor, info, digest, body)

    async def _put_state(
        self,
        key: str,
        body: bytes,
        *,
        envelope: RunStateEnvelope,
        digest: str,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectInfo:
        try:
            return await self._objects.put(
                key,
                body,
                content_type=RUN_STATE_CONTENT_TYPE,
                metadata=_state_metadata(envelope, digest),
                if_none_match=if_none_match,
                if_match=if_match,
            )
        except (ObjectStoreUnavailable, TimeoutError) as error:
            # A lost response can follow a durable write. Never repeat a mutation
            # using its old token, or accept another writer's bytes as our receipt.
            try:
                observed = await self._objects.stat(key)
                actual, info = await _read_object(self._objects, key, max_bytes=self._max_state_bytes)
            except (ObjectNotFound, ObjectStoreUnavailable, TimeoutError) as read_error:
                raise error from read_error
            if info.version != observed.version:
                raise StaleStateWriter("Run state changed during write reconciliation") from error
            if actual != body:
                if info.version != if_match:
                    raise StaleStateWriter("Run state changed after an uncertain write") from error
                raise error
            _verify_info(info, key=key, body=body, content_type=RUN_STATE_CONTENT_TYPE)
            _verify_state_metadata(info, envelope=envelope, digest=digest)
            logger.info(
                "run_state_write_reconciled", extra={"run_id": envelope.run_id, "writer_fence": envelope.writer_fence}
            )
            return info

    def _require_bounded(self, body: bytes) -> None:
        if len(body) > self._max_state_bytes:
            raise RunObjectError("Run state exceeds the configured size limit")


class RunPayloadStore:
    def __init__(self, objects: ObjectStore, *, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> None:
        if max_payload_bytes < 1:
            raise ValueError("max_payload_bytes must be positive")
        self._objects = objects
        self._max_payload_bytes = max_payload_bytes

    async def create(self, organization_id: str, envelope: RunPayloadEnvelope) -> RunPayloadObjectRef:
        body = canonical_model_bytes(envelope)
        if len(body) > self._max_payload_bytes:
            raise RunObjectError("Run payload exceeds the configured size limit")
        digest = hashlib.sha256(body).hexdigest()
        key = run_payload_key(organization_id, envelope.run_id, envelope.payload_kind, digest)
        metadata = {
            "schema-version": envelope.schema_version,
            "run-id": envelope.run_id,
            "payload-kind": envelope.payload_kind,
            "digest-sha256": digest,
        }
        try:
            info = await self._objects.put(
                key,
                body,
                content_type=RUN_PAYLOAD_CONTENT_TYPE,
                metadata=metadata,
                if_none_match=True,
            )
        except ObjectConflict as error:
            existing = await self.read(
                organization_id,
                RunPayloadObjectRef(
                    object_key=key,
                    digest_sha256=digest,
                    size_bytes=len(body),
                    content_type=RUN_PAYLOAD_CONTENT_TYPE,
                    schema_version=envelope.schema_version,
                ),
            )
            if existing != envelope:
                raise RunObjectIntegrityError("content-addressed Run payload does not match existing bytes") from error
        else:
            _verify_info(info, key=key, body=body, content_type=RUN_PAYLOAD_CONTENT_TYPE)
            if any(info.metadata.get(name) != value for name, value in metadata.items()):
                raise RunObjectIntegrityError("object store returned inconsistent Run payload metadata")
        return RunPayloadObjectRef(
            object_key=key,
            digest_sha256=digest,
            size_bytes=len(body),
            content_type=RUN_PAYLOAD_CONTENT_TYPE,
            schema_version=envelope.schema_version,
        )

    async def read(self, organization_id: str, reference: RunPayloadObjectRef) -> RunPayloadEnvelope:
        _validate_run_payload_reference_format(reference)
        if not reference.object_key.startswith(f"organizations/{organization_id}/runs/"):
            raise RunObjectIntegrityError("Run payload reference is outside the authorized organization")
        body, info = await _read_object(self._objects, reference.object_key, max_bytes=self._max_payload_bytes)
        _verify_info(info, key=reference.object_key, body=body, content_type=RUN_PAYLOAD_CONTENT_TYPE)
        digest = hashlib.sha256(body).hexdigest()
        if len(body) != reference.size_bytes or digest != reference.digest_sha256:
            raise RunObjectIntegrityError("Run payload bytes do not match their immutable reference")
        try:
            envelope = decode_canonical_model(body, _PAYLOAD_ADAPTER)
        except DurableObjectCodecError as error:
            raise RunObjectIntegrityError("Run payload body is invalid") from error
        validate_run_payload_reference(
            organization_id,
            envelope.run_id,
            envelope.payload_kind,
            reference,
        )
        expected_metadata = {
            "schema-version": envelope.schema_version,
            "run-id": envelope.run_id,
            "payload-kind": envelope.payload_kind,
            "digest-sha256": digest,
        }
        if any(info.metadata.get(key) != value for key, value in expected_metadata.items()):
            raise RunObjectIntegrityError("Run payload object metadata is invalid")
        return envelope

    async def verify_reference(
        self,
        organization_id: str,
        run_id: str,
        payload_kind: Literal["input", "output"],
        reference: RunPayloadObjectRef,
    ) -> RunPayloadEnvelope:
        """Read and verify one exact Run-owned payload reference."""

        validate_run_payload_reference(organization_id, run_id, payload_kind, reference)
        envelope = await self.read(organization_id, reference)
        if envelope.run_id != run_id or envelope.payload_kind != payload_kind:
            raise RunObjectIntegrityError("Run payload envelope does not match its selected owner and kind")
        return envelope


def run_state_key(organization_id: str, run_id: str) -> str:
    return f"organizations/{organization_id}/runs/{run_id}/state.json"


def run_payload_key(organization_id: str, run_id: str, payload_kind: str, digest_sha256: str) -> str:
    return f"organizations/{organization_id}/runs/{run_id}/payloads/{payload_kind}/{digest_sha256}.json"


def validate_run_payload_reference(
    organization_id: str,
    run_id: str,
    payload_kind: Literal["input", "output"],
    reference: RunPayloadObjectRef,
) -> None:
    """Require a payload reference to name the exact supported Run-owned object."""

    _validate_run_payload_reference_format(reference)
    expected_key = run_payload_key(organization_id, run_id, payload_kind, reference.digest_sha256)
    if reference.object_key != expected_key:
        raise RunObjectIntegrityError("Run payload reference is not owned by the selected Run")


def _validate_run_payload_reference_format(reference: RunPayloadObjectRef) -> None:
    if reference.content_type != RUN_PAYLOAD_CONTENT_TYPE:
        raise RunObjectIntegrityError("Run payload reference content type is invalid")
    if reference.schema_version != "1":
        raise RunObjectIntegrityError("Run payload reference schema version is unsupported")


async def _read_object(objects: ObjectStore, key: str, *, max_bytes: int) -> tuple[bytes, ObjectInfo]:
    async with objects.open(key) as reader:
        info = reader.info
        if info.size > max_bytes:
            raise RunObjectIntegrityError("Run object exceeds the configured size limit")
        chunks: list[bytes] = []
        size = 0
        async for chunk in reader:
            size += len(chunk)
            if size > max_bytes:
                raise RunObjectIntegrityError("Run object exceeds the configured size limit")
            chunks.append(chunk)
    body = b"".join(chunks)
    if len(body) != info.size:
        raise RunObjectIntegrityError("Run object body size does not match object metadata")
    return body, info


def _state_metadata(envelope: RunStateEnvelope, digest: str) -> dict[str, str]:
    return {
        "schema-version": envelope.schema_version,
        "run-id": envelope.run_id,
        "thread-id": envelope.thread_id,
        "checkpoint-seq": str(envelope.checkpoint_seq),
        "writer-fence": str(envelope.writer_fence),
        "digest-sha256": digest,
    }


def _verify_state_metadata(info: ObjectInfo, *, envelope: RunStateEnvelope, digest: str) -> None:
    expected = _state_metadata(envelope, digest)
    for key, value in expected.items():
        if info.metadata.get(key) != value:
            raise RunObjectIntegrityError(f"Run state metadata field {key} is invalid")


def _verify_info(info: ObjectInfo, *, key: str, body: bytes, content_type: str) -> None:
    if info.key != key or info.size != len(body) or info.content_type != content_type or not info.version:
        raise RunObjectIntegrityError("object store returned inconsistent Run object metadata")


__all__ = [
    "RUN_PAYLOAD_CONTENT_TYPE",
    "RUN_STATE_CONTENT_TYPE",
    "RunObjectError",
    "RunObjectIntegrityError",
    "RunPayloadStore",
    "RunStateStore",
    "StaleStateWriter",
    "StoredRunState",
    "run_payload_key",
    "run_state_key",
    "validate_run_payload_reference",
]
