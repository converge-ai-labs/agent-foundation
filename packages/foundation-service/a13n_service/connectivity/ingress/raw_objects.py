"""Protected, non-public raw Ingress evidence storage."""

from __future__ import annotations

import hashlib
from datetime import datetime

from a13n_service.connectivity.errors import NativeError
from a13n_service.storage.object_store import ObjectConflict, ObjectInfo, ObjectStore, ObjectStoreError

from .admission_domain import ProtectedRawRef

_CONTENT_TYPE = "application/octet-stream"


class IngressRawObjectStore:
    def __init__(self, objects: ObjectStore) -> None:
        self._objects = objects

    async def retain(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        ingress_id: str,
        identity_digest: str,
        body: bytes,
        expires_at: datetime,
    ) -> ProtectedRawRef:
        digest = hashlib.sha256(body).hexdigest()
        key = (
            f"tenants/{organization_id}/workspaces/{workspace_id}/connectivity/raw/version-1/"
            f"{ingress_id}/{identity_digest}/{digest}"
        )
        metadata = {
            "ingress-id": ingress_id,
            "body-sha256": digest,
            "size-bytes": str(len(body)),
        }
        try:
            try:
                info = await self._objects.put(
                    key,
                    body,
                    content_type=_CONTENT_TYPE,
                    metadata=metadata,
                    if_none_match=True,
                )
            except ObjectConflict:
                info = await self._objects.stat(key)
            _verify(info, key=key, metadata=metadata, size=len(body))
        except (ObjectStoreError, ValueError) as error:
            raise NativeError(
                "raw_retention_unavailable",
                "Protected provider evidence could not be retained.",
                status_code=503,
            ) from error
        return ProtectedRawRef(
            object_key=key,
            size_bytes=len(body),
            content_sha256=digest,
            expires_at=expires_at,
        )

    async def delete(self, raw_ref: ProtectedRawRef) -> None:
        try:
            await self._objects.delete(raw_ref.object_key)
        except ObjectStoreError:
            return


def _verify(info: ObjectInfo, *, key: str, metadata: dict[str, str], size: int) -> None:
    if info.key != key or info.size != size or info.content_type != _CONTENT_TYPE or dict(info.metadata) != metadata:
        raise ValueError("raw Ingress object metadata does not match relational authority")
