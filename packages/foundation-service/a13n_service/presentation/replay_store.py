"""Create-only object storage and verified reads for retained Run replay."""

from __future__ import annotations

import hashlib

from pydantic import TypeAdapter

from a13n_service.interactions.codec import (
    DurableObjectCodecError,
    canonical_model_bytes,
    decode_canonical_model,
)
from a13n_service.storage import (
    ObjectConflict,
    ObjectInfo,
    ObjectNotFound,
    ObjectStore,
    ObjectStoreError,
)

from .domain import RetainedItem, RunReplaySnapshot, run_replay_key, run_stream_key_digest

RUN_REPLAY_CONTENT_TYPE = "application/vnd.converge.run-replay+json"
DEFAULT_MAX_REPLAY_BYTES = 32 * 1024 * 1024
_SNAPSHOT_ADAPTER = TypeAdapter(RunReplaySnapshot)


class RunReplayError(RuntimeError):
    """A replay snapshot could not be published or verified safely."""


class RunReplayUnavailable(RunReplayError):
    """No complete retained replay is available for the selected Run."""


class RunReplayIntegrityError(RunReplayError):
    """A retained replay object contradicts its immutable contract."""


class RunReplayStore:
    """Store one immutable, complete replay snapshot at its deterministic Run key."""

    def __init__(self, objects: ObjectStore, *, max_replay_bytes: int = DEFAULT_MAX_REPLAY_BYTES) -> None:
        if max_replay_bytes < 1:
            raise ValueError("max_replay_bytes must be positive")
        self._objects = objects
        self._max_replay_bytes = max_replay_bytes

    async def create(self, tenant_id: str, snapshot: RunReplaySnapshot) -> RunReplaySnapshot:
        self._validate_tenant_identity(tenant_id, snapshot)
        try:
            body = canonical_model_bytes(snapshot)
        except DurableObjectCodecError as error:
            raise RunReplayIntegrityError("Run replay snapshot is not canonical JSON") from error
        if len(body) > self._max_replay_bytes:
            raise RunReplayUnavailable("Run replay snapshot exceeds the configured size limit")
        digest = hashlib.sha256(body).hexdigest()
        key = run_replay_key(tenant_id, snapshot.run_id)
        metadata = {
            "schema-version": snapshot.schema_version,
            "run-id": snapshot.run_id,
            "digest-sha256": digest,
        }
        try:
            info = await self._objects.put(
                key,
                body,
                content_type=RUN_REPLAY_CONTENT_TYPE,
                metadata=metadata,
                if_none_match=True,
            )
        except ObjectConflict as error:
            existing = await self.read(tenant_id, snapshot.run_id, expected_thread_id=snapshot.thread_id)
            if existing != snapshot:
                raise RunReplayIntegrityError("replay key already contains a different snapshot") from error
            return existing
        self._verify_info(info, key=key, body=body, expected_metadata=metadata)
        return snapshot

    async def read(
        self,
        tenant_id: str,
        run_id: str,
        *,
        expected_thread_id: str | None = None,
    ) -> RunReplaySnapshot:
        key = run_replay_key(tenant_id, run_id)
        try:
            body, info = await self._read_bounded(key)
        except ObjectNotFound as error:
            raise RunReplayUnavailable("retained Run replay is unavailable") from error
        except ObjectStoreError as error:
            raise RunReplayIntegrityError("retained Run replay could not be read") from error
        digest = hashlib.sha256(body).hexdigest()
        expected_metadata = {
            "schema-version": "1",
            "run-id": run_id,
            "digest-sha256": digest,
        }
        self._verify_info(info, key=key, body=body, expected_metadata=expected_metadata)
        try:
            snapshot = decode_canonical_model(body, _SNAPSHOT_ADAPTER)
        except DurableObjectCodecError as error:
            raise RunReplayIntegrityError("retained Run replay body is invalid") from error
        if snapshot.run_id != run_id or (expected_thread_id is not None and snapshot.thread_id != expected_thread_id):
            raise RunReplayIntegrityError("retained Run replay identity does not match relational authority")
        self._validate_tenant_identity(tenant_id, snapshot)
        return snapshot

    async def read_item(
        self,
        tenant_id: str,
        run_id: str,
        item_id: str,
        *,
        expected_thread_id: str | None = None,
    ) -> RetainedItem:
        snapshot = await self.read(
            tenant_id,
            run_id,
            expected_thread_id=expected_thread_id,
        )
        for item in snapshot.items:
            if item.id == item_id:
                return item
        raise RunReplayUnavailable("retained Item is unavailable")

    async def _read_bounded(self, key: str) -> tuple[bytes, ObjectInfo]:
        async with self._objects.open(key) as reader:
            if reader.info.size > self._max_replay_bytes:
                raise RunReplayIntegrityError("retained Run replay exceeds the configured size limit")
            chunks: list[bytes] = []
            size = 0
            async for chunk in reader:
                size += len(chunk)
                if size > self._max_replay_bytes:
                    raise RunReplayIntegrityError("retained Run replay exceeds the configured size limit")
                chunks.append(chunk)
            body = b"".join(chunks)
            info = reader.info
        if len(body) != info.size:
            raise RunReplayIntegrityError("retained Run replay size does not match object metadata")
        return body, info

    @staticmethod
    def _verify_info(
        info: ObjectInfo,
        *,
        key: str,
        body: bytes,
        expected_metadata: dict[str, str],
    ) -> None:
        if (
            info.key != key
            or info.size != len(body)
            or info.content_type != RUN_REPLAY_CONTENT_TYPE
            or not info.version
            or any(info.metadata.get(name) != value for name, value in expected_metadata.items())
        ):
            raise RunReplayIntegrityError("object store returned inconsistent Run replay metadata")

    @staticmethod
    def _validate_tenant_identity(tenant_id: str, snapshot: RunReplaySnapshot) -> None:
        if snapshot.stream_key_digest_sha256 != run_stream_key_digest(tenant_id, snapshot.run_id):
            raise RunReplayIntegrityError("retained Run replay stream identity is outside the authorized tenant")


__all__ = [
    "DEFAULT_MAX_REPLAY_BYTES",
    "RUN_REPLAY_CONTENT_TYPE",
    "RunReplayError",
    "RunReplayIntegrityError",
    "RunReplayStore",
    "RunReplayUnavailable",
]
