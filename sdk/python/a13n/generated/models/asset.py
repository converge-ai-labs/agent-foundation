from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.run_output_asset_source import RunOutputAssetSource
    from ..models.uploaded_asset_source import UploadedAssetSource


T = TypeVar("T", bound="Asset")


@_attrs_define(repr=False)
class Asset:
    """One exact immutable binary publication and its lifecycle marker.

    Attributes:
        content_sha256 (str):
        created_at (datetime.datetime):
        deleted_at (datetime.datetime | None):
        filename (str):
        id (str):
        media_type (str):
        organization_id (str):
        size_bytes (int):
        source (RunOutputAssetSource | UploadedAssetSource):
        workspace_id (str):
    """

    content_sha256: str
    created_at: datetime.datetime
    deleted_at: datetime.datetime | None
    filename: str
    id: str
    media_type: str
    organization_id: str
    size_bytes: int
    source: RunOutputAssetSource | UploadedAssetSource
    workspace_id: str

    def to_dict(self) -> dict[str, Any]:
        from ..models.uploaded_asset_source import UploadedAssetSource

        content_sha256 = self.content_sha256

        created_at = self.created_at.isoformat()

        deleted_at: str | None
        if isinstance(self.deleted_at, datetime.datetime):
            deleted_at = self.deleted_at.isoformat()
        else:
            deleted_at = self.deleted_at

        filename = self.filename

        id = self.id

        media_type = self.media_type

        organization_id = self.organization_id

        size_bytes = self.size_bytes

        source: dict[str, Any]
        if isinstance(self.source, UploadedAssetSource):
            source = self.source.to_dict()
        else:
            source = self.source.to_dict()

        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "content_sha256": content_sha256,
                "created_at": created_at,
                "deleted_at": deleted_at,
                "filename": filename,
                "id": id,
                "media_type": media_type,
                "organization_id": organization_id,
                "size_bytes": size_bytes,
                "source": source,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_output_asset_source import RunOutputAssetSource
        from ..models.uploaded_asset_source import UploadedAssetSource

        d = dict(src_dict)
        content_sha256 = d.pop("content_sha256")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_deleted_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                deleted_at_type_0 = datetime.datetime.fromisoformat(data)

                return deleted_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        deleted_at = _parse_deleted_at(d.pop("deleted_at"))

        filename = d.pop("filename")

        id = d.pop("id")

        media_type = d.pop("media_type")

        organization_id = d.pop("organization_id")

        size_bytes = d.pop("size_bytes")

        def _parse_source(data: object) -> RunOutputAssetSource | UploadedAssetSource:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = UploadedAssetSource.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            source_type_1 = RunOutputAssetSource.from_dict(data)

            return source_type_1

        source = _parse_source(d.pop("source"))

        workspace_id = d.pop("workspace_id")

        asset = cls(
            content_sha256=content_sha256,
            created_at=created_at,
            deleted_at=deleted_at,
            filename=filename,
            id=id,
            media_type=media_type,
            organization_id=organization_id,
            size_bytes=size_bytes,
            source=source,
            workspace_id=workspace_id,
        )

        return asset
