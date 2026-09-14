from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.skill_package_manifest import SkillPackageManifest


T = TypeVar("T", bound="SkillUploadReceipt")


@_attrs_define(repr=False)
class SkillUploadReceipt:
    """
    Attributes:
        archive_sha256 (str):
        consumed_by_revision_id (None | str):
        expires_at (datetime.datetime):
        manifest (SkillPackageManifest):
        upload_id (str):
        workspace_id (str):
    """

    archive_sha256: str
    consumed_by_revision_id: str | None
    expires_at: datetime.datetime
    manifest: SkillPackageManifest
    upload_id: str
    workspace_id: str

    def to_dict(self) -> dict[str, Any]:
        archive_sha256 = self.archive_sha256

        consumed_by_revision_id: str | None
        consumed_by_revision_id = self.consumed_by_revision_id

        expires_at = self.expires_at.isoformat()

        manifest = self.manifest.to_dict()

        upload_id = self.upload_id

        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "archive_sha256": archive_sha256,
                "consumed_by_revision_id": consumed_by_revision_id,
                "expires_at": expires_at,
                "manifest": manifest,
                "upload_id": upload_id,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.skill_package_manifest import SkillPackageManifest

        d = dict(src_dict)
        archive_sha256 = d.pop("archive_sha256")

        def _parse_consumed_by_revision_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        consumed_by_revision_id = _parse_consumed_by_revision_id(d.pop("consumed_by_revision_id"))

        expires_at = datetime.datetime.fromisoformat(d.pop("expires_at"))

        manifest = SkillPackageManifest.from_dict(d.pop("manifest"))

        upload_id = d.pop("upload_id")

        workspace_id = d.pop("workspace_id")

        skill_upload_receipt = cls(
            archive_sha256=archive_sha256,
            consumed_by_revision_id=consumed_by_revision_id,
            expires_at=expires_at,
            manifest=manifest,
            upload_id=upload_id,
            workspace_id=workspace_id,
        )

        return skill_upload_receipt
