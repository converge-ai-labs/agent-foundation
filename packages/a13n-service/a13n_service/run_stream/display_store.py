"""Conditional, compressed display publication with durable cursor recovery."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import TypeAdapter

from a13n_service.storage import ObjectInfo, ObjectStore, ObjectStoreUnavailable
from a13n_service.storage.codec import (
    COMPRESSED_JSON_CONTENT_TYPE,
    COMPRESSED_JSON_ENCODING,
    DurableObjectCodecError,
    DurableObjectSizeError,
    compressed_size_limit,
    decode_compressed_model,
    encode_compressed_model,
)

from .display_model import DisplayIntegrityError, DisplayLimitExceeded, RunDisplaySnapshot, validate_successor
from .redis import run_stream_key_digest_sha256

_ADAPTER = TypeAdapter(RunDisplaySnapshot)


@dataclass(frozen=True, slots=True)
class StoredDisplay:
    snapshot: RunDisplaySnapshot
    object_version: str


def display_key(organization_id: str, run_id: str) -> str:
    return f"organizations/{organization_id}/runs/{run_id}/display_messages.json"


class RunDisplayStore:
    def __init__(self, objects: ObjectStore, *, max_items: int = 2048, max_bytes: int = 16 * 1024 * 1024) -> None:
        if min(max_items, max_bytes) < 1:
            raise ValueError("display bounds must be positive")
        self._objects = objects
        self._max_items = max_items
        self._max_bytes = max_bytes
        self._max_encoded_bytes = compressed_size_limit(max_bytes)

    async def read(self, organization_id: str, run_id: str, *, expected_thread_id: str) -> StoredDisplay:
        key = display_key(organization_id, run_id)
        async with self._objects.open(key) as reader:
            info = reader.info
            self._verify_info(info, key, run_id)
            chunks: list[bytes] = []
            size = 0
            async for chunk in reader:
                size += len(chunk)
                if size > self._max_encoded_bytes:
                    raise DisplayLimitExceeded("display encoded bytes exceed their bound")
                chunks.append(chunk)
        if size != info.size:
            raise DisplayIntegrityError("display size differs from object metadata")
        try:
            snapshot = await decode_compressed_model(
                b"".join(chunks),
                _ADAPTER,
                max_bytes=self._max_bytes,
                digest_sha256=info.metadata["digest-sha256"],
            )
        except DurableObjectSizeError as error:
            raise DisplayLimitExceeded("display decoded bytes exceed their bound") from error
        except DurableObjectCodecError as error:
            raise DisplayIntegrityError("display body is invalid") from error
        self._verify_identity(snapshot, organization_id, run_id, expected_thread_id)
        return StoredDisplay(snapshot, info.version)

    async def publish(
        self,
        organization_id: str,
        snapshot: RunDisplaySnapshot,
        *,
        previous: StoredDisplay | None,
    ) -> StoredDisplay:
        validate_successor(None if previous is None else previous.snapshot, snapshot)
        self._verify_identity(snapshot, organization_id, snapshot.run_id, snapshot.thread_id)
        try:
            body, digest = await encode_compressed_model(snapshot, max_bytes=self._max_bytes)
        except DurableObjectSizeError as error:
            raise DisplayLimitExceeded("display decoded bytes exceed their bound") from error
        except DurableObjectCodecError as error:
            raise DisplayIntegrityError("display cannot be encoded") from error
        key = display_key(organization_id, snapshot.run_id)
        metadata = {
            "storage-encoding": COMPRESSED_JSON_ENCODING,
            "schema-version": "1",
            "run-id": snapshot.run_id,
            "digest-sha256": digest,
        }
        try:
            info = await self._objects.put(
                key,
                body,
                content_type=COMPRESSED_JSON_CONTENT_TYPE,
                metadata=metadata,
                if_none_match=previous is None,
                if_match=None if previous is None else previous.object_version,
            )
        except ObjectStoreUnavailable:
            # A lost acknowledgement is safe only after verifying the exact write.
            recovered = await self.read(organization_id, snapshot.run_id, expected_thread_id=snapshot.thread_id)
            if recovered.snapshot != snapshot:
                raise
            return recovered
        self._verify_info(info, key, snapshot.run_id)
        if info.size != len(body) or info.metadata.get("digest-sha256") != digest:
            raise DisplayIntegrityError("display publication acknowledgement differs from submitted bytes")
        return StoredDisplay(snapshot, info.version)

    def _verify_identity(self, snapshot: RunDisplaySnapshot, organization_id: str, run_id: str, thread_id: str) -> None:
        if (snapshot.run_id, snapshot.thread_id, snapshot.stream_key_digest_sha256) != (
            run_id,
            thread_id,
            run_stream_key_digest_sha256(organization_id, run_id),
        ):
            raise DisplayIntegrityError("display belongs to another Run or stream")
        if len(snapshot.items) > self._max_items:
            raise DisplayLimitExceeded("display Item count exceeds its bound")

    def _verify_info(self, info: ObjectInfo, key: str, run_id: str) -> None:
        if info.size > self._max_encoded_bytes:
            raise DisplayLimitExceeded("display encoded bytes exceed their bound")
        if (
            info.key != key
            or not info.version
            or info.content_type != COMPRESSED_JSON_CONTENT_TYPE
            or info.metadata.get("storage-encoding") != COMPRESSED_JSON_ENCODING
            or info.metadata.get("schema-version") != "1"
            or info.metadata.get("run-id") != run_id
            or not info.metadata.get("digest-sha256")
        ):
            raise DisplayIntegrityError("display object metadata is invalid")
