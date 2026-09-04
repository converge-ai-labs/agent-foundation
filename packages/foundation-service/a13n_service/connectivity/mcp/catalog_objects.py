"""Immutable protected MCP tool-catalog objects."""

from __future__ import annotations

import hashlib

from a13n_service.storage.object_store import ObjectConflict, ObjectStore, ObjectStoreError

from .errors import MCPConnectionError


class MCPCatalogObjectStore:
    def __init__(self, objects: ObjectStore) -> None:
        self._objects = objects

    async def retain(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        connection_id: str,
        body: bytes,
    ) -> tuple[str, str, bool]:
        digest = hashlib.sha256(body).hexdigest()
        key = (
            f"tenants/{organization_id}/workspaces/{workspace_id}/connectivity/mcp-catalogs/version-1/"
            f"{connection_id}/{digest}.json"
        )
        metadata = {
            "mcp-connection-id": connection_id,
            "content-sha256": digest,
            "size-bytes": str(len(body)),
        }
        try:
            created = False
            try:
                info = await self._objects.put(
                    key,
                    body,
                    content_type="application/json",
                    metadata=metadata,
                    if_none_match=True,
                )
                created = True
            except ObjectConflict:
                info = await self._objects.stat(key)
            if (
                info.key != key
                or info.size != len(body)
                or info.content_type != "application/json"
                or dict(info.metadata) != metadata
            ):
                raise ValueError("catalog object mismatch")
        except (ObjectStoreError, ValueError) as error:
            raise MCPConnectionError(
                "catalog_storage_unavailable",
                "MCP tool catalog could not be retained.",
                status_code=503,
            ) from error
        return key, digest, created

    async def delete(self, key: str) -> None:
        try:
            await self._objects.delete(key)
        except ObjectStoreError:
            return
