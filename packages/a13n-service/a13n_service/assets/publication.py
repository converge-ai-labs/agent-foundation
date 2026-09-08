"""Bounded candidate preparation shared by HTTP and Environment publications."""

from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from a13n_service.temporal import Clock, utc_now

from .domain import Asset, AssetSource, new_asset_id, normalize_asset_filename, normalize_media_type
from .errors import asset_content_invalid, asset_media_type_invalid
from .objects import AssetObjectStore
from .staging import AssetStaging, StagedAssetContent


@dataclass(frozen=True, slots=True)
class StagedPublication:
    asset: Asset
    content: StagedAssetContent


class AssetPublisher:
    """Prepare and publish bytes; callers own relational commit and reconciliation."""

    def __init__(
        self, objects: AssetObjectStore, staging: AssetStaging, *, max_size_bytes: int, clock: Clock = utc_now
    ) -> None:
        self._objects = objects
        self._staging = staging
        self._max_size_bytes = max_size_bytes
        self._clock = clock

    @asynccontextmanager
    async def stage(
        self,
        *,
        organization_id: str,
        workspace_id: str,
        source: AssetSource,
        filename: str,
        media_type: str | None,
        body: AsyncIterable[bytes],
        content_length: int | None,
    ) -> AsyncIterator[StagedPublication]:
        try:
            filename = normalize_asset_filename(filename)
        except ValueError as error:
            raise asset_content_invalid() from error
        try:
            media_type = normalize_media_type(media_type)
        except ValueError as error:
            raise asset_media_type_invalid() from error
        content = await self._staging.stage_upload(
            body, max_size_bytes=self._max_size_bytes, content_length=content_length
        )
        try:
            if media_type != "application/octet-stream" and content.detected_media_type not in {None, media_type}:
                raise asset_media_type_invalid()
            yield StagedPublication(
                Asset(
                    id=new_asset_id(),
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    filename=filename,
                    media_type=media_type,
                    size_bytes=content.size_bytes,
                    content_sha256=content.content_sha256,
                    source=source,
                    created_at=self._clock(),
                    deleted_at=None,
                ),
                content,
            )
        finally:
            await content.remove()

    async def publish(self, candidate: StagedPublication) -> None:
        asset = candidate.asset
        await self._objects.publish(
            asset_id=asset.id,
            organization_id=asset.organization_id,
            workspace_id=asset.workspace_id,
            content=candidate.content,
        )
