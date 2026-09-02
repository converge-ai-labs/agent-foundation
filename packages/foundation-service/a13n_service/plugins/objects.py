"""Content-addressed authoritative storage for Plugin Wheels."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from a13n_service.storage.object_store import (
    ObjectConflict,
    ObjectInfo,
    ObjectReader,
    ObjectStore,
    ObjectStoreError,
)

from .errors import plugin_artifact_unavailable
from .staging import StagedPluginWheel

PLUGIN_WHEEL_CONTENT_TYPE = "application/octet-stream"


class PluginObjectStore:
    def __init__(self, objects: ObjectStore) -> None:
        self._objects = objects

    async def publish(self, wheel: StagedPluginWheel) -> str:
        key = plugin_artifact_key(wheel.content_digest)
        metadata = {
            "content-sha256": wheel.content_digest,
            "size-bytes": str(wheel.size_bytes),
        }
        try:
            try:
                info = await self._objects.put(
                    key,
                    wheel.chunks(),
                    content_type=PLUGIN_WHEEL_CONTENT_TYPE,
                    metadata=metadata,
                    if_none_match=True,
                )
            except ObjectConflict:
                info = await self._objects.stat(key)
            _verify_info(info, key=key, metadata=metadata, size_bytes=wheel.size_bytes)
        except (ObjectStoreError, ValueError) as error:
            raise plugin_artifact_unavailable() from error
        return key

    @asynccontextmanager
    async def open_verified(
        self,
        *,
        artifact_ref: str,
        content_digest: str,
    ) -> AsyncGenerator[ObjectReader]:
        """Open one authoritative Wheel after validating its content-addressed envelope."""

        expected_key = plugin_artifact_key(content_digest)
        if artifact_ref != expected_key:
            raise plugin_artifact_unavailable()
        try:
            async with self._objects.open(artifact_ref) as reader:
                metadata = {
                    "content-sha256": content_digest,
                    "size-bytes": str(reader.info.size),
                }
                _verify_info(
                    reader.info,
                    key=expected_key,
                    metadata=metadata,
                    size_bytes=reader.info.size,
                )
                yield reader
        except (ObjectStoreError, ValueError) as error:
            raise plugin_artifact_unavailable() from error


def plugin_artifact_key(content_digest: str) -> str:
    return f"plugins/artifacts/v1/sha256/{content_digest}.whl"


def _verify_info(info: ObjectInfo, *, key: str, metadata: dict[str, str], size_bytes: int) -> None:
    if (
        info.key != key
        or info.size != size_bytes
        or info.content_type != PLUGIN_WHEEL_CONTENT_TYPE
        or dict(info.metadata) != metadata
    ):
        raise ValueError("Plugin artifact object metadata does not match the content identity")
