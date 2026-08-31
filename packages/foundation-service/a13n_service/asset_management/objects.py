"""Derived Asset object layout and integrity-preserving object operations."""

from __future__ import annotations

from a13n_service.storage.object_store import (
    ObjectInfo,
    ObjectStore,
    ObjectStoreError,
)

from .domain import Asset
from .errors import asset_content_unavailable
from .staging import AssetStaging, StagedAssetContent

ASSET_OBJECT_DESTINATION = "primary"
ASSET_OBJECT_CONTENT_TYPE = "application/octet-stream"


class AssetObjectStore:
    def __init__(self, objects: ObjectStore, staging: AssetStaging) -> None:
        self._objects = objects
        self._staging = staging

    async def publish(
        self,
        *,
        asset_id: str,
        organization_id: str,
        workspace_id: str,
        content: StagedAssetContent,
    ) -> None:
        key = asset_content_key(
            organization_id=organization_id,
            workspace_id=workspace_id,
            asset_id=asset_id,
        )
        metadata = asset_object_metadata(
            asset_id=asset_id,
            workspace_id=workspace_id,
            size_bytes=content.size_bytes,
            content_sha256=content.content_sha256,
        )
        try:
            info = await self._objects.put(
                key,
                content.chunks(),
                content_type=ASSET_OBJECT_CONTENT_TYPE,
                metadata=metadata,
                if_none_match=True,
            )
            _verify_info(info, key=key, metadata=metadata, size_bytes=content.size_bytes)
        except (ObjectStoreError, ValueError) as error:
            raise asset_content_unavailable() from error

    async def prepare_verified_content(self, asset: Asset) -> StagedAssetContent:
        key = asset_content_key(
            organization_id=asset.organization_id,
            workspace_id=asset.workspace_id,
            asset_id=asset.id,
        )
        metadata = asset_object_metadata(
            asset_id=asset.id,
            workspace_id=asset.workspace_id,
            size_bytes=asset.size_bytes,
            content_sha256=asset.content_sha256,
        )
        try:
            async with self._objects.open(key) as reader:
                _verify_info(reader.info, key=key, metadata=metadata, size_bytes=asset.size_bytes)
                return await self._staging.stage_verified(
                    reader,
                    expected_size=asset.size_bytes,
                    expected_digest=asset.content_sha256,
                )
        except (ObjectStoreError, ValueError) as error:
            raise asset_content_unavailable() from error

    async def delete(self, asset: Asset) -> None:
        await self.delete_content(
            organization_id=asset.organization_id,
            workspace_id=asset.workspace_id,
            asset_id=asset.id,
        )

    async def delete_content(self, *, organization_id: str, workspace_id: str, asset_id: str) -> None:
        key = asset_content_key(
            organization_id=organization_id,
            workspace_id=workspace_id,
            asset_id=asset_id,
        )
        try:
            await self._objects.delete(key)
        except ObjectStoreError as error:
            raise asset_content_unavailable() from error

    async def delete_candidate(
        self,
        *,
        asset_id: str,
        organization_id: str,
        workspace_id: str,
    ) -> None:
        try:
            await self.delete_content(
                organization_id=organization_id,
                workspace_id=workspace_id,
                asset_id=asset_id,
            )
        except Exception:
            return


def asset_content_key(*, organization_id: str, workspace_id: str, asset_id: str) -> str:
    return f"tenants/{organization_id}/workspaces/{workspace_id}/assets/version-1/{asset_id}/content"


def asset_object_metadata(
    *,
    asset_id: str,
    workspace_id: str,
    size_bytes: int,
    content_sha256: str,
) -> dict[str, str]:
    return {
        "asset-id": asset_id,
        "workspace-id": workspace_id,
        "size-bytes": str(size_bytes),
        "content-sha256": content_sha256,
    }


def _verify_info(info: ObjectInfo, *, key: str, metadata: dict[str, str], size_bytes: int) -> None:
    if (
        info.key != key
        or info.size != size_bytes
        or info.content_type != ASSET_OBJECT_CONTENT_TYPE
        or dict(info.metadata) != metadata
    ):
        raise ValueError("Asset object metadata does not match relational authority")
