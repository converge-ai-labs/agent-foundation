from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.git_hub_skill_import_provenance import GitHubSkillImportProvenance
    from ..models.principal_ref import PrincipalRef
    from ..models.skill_package_manifest import SkillPackageManifest
    from ..models.zip_skill_import_provenance import ZipSkillImportProvenance


T = TypeVar("T", bound="SkillRevision")


@_attrs_define(repr=False)
class SkillRevision:
    """
    Attributes:
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        id (str):
        imported_from (GitHubSkillImportProvenance | ZipSkillImportProvenance):
        manifest (SkillPackageManifest):
        organization_id (str):
        skill_id (str):
        version (int):
        workspace_id (str):
    """

    created_at: datetime.datetime
    created_by: PrincipalRef
    id: str
    imported_from: GitHubSkillImportProvenance | ZipSkillImportProvenance
    manifest: SkillPackageManifest
    organization_id: str
    skill_id: str
    version: int
    workspace_id: str

    def to_dict(self) -> dict[str, Any]:
        from ..models.zip_skill_import_provenance import ZipSkillImportProvenance

        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        id = self.id

        imported_from: dict[str, Any]
        if isinstance(self.imported_from, ZipSkillImportProvenance):
            imported_from = self.imported_from.to_dict()
        else:
            imported_from = self.imported_from.to_dict()

        manifest = self.manifest.to_dict()

        organization_id = self.organization_id

        skill_id = self.skill_id

        version = self.version

        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "created_by": created_by,
                "id": id,
                "imported_from": imported_from,
                "manifest": manifest,
                "organization_id": organization_id,
                "skill_id": skill_id,
                "version": version,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.git_hub_skill_import_provenance import GitHubSkillImportProvenance
        from ..models.principal_ref import PrincipalRef
        from ..models.skill_package_manifest import SkillPackageManifest
        from ..models.zip_skill_import_provenance import ZipSkillImportProvenance

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        id = d.pop("id")

        def _parse_imported_from(data: object) -> GitHubSkillImportProvenance | ZipSkillImportProvenance:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                imported_from_type_0 = ZipSkillImportProvenance.from_dict(data)

                return imported_from_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            imported_from_type_1 = GitHubSkillImportProvenance.from_dict(data)

            return imported_from_type_1

        imported_from = _parse_imported_from(d.pop("imported_from"))

        manifest = SkillPackageManifest.from_dict(d.pop("manifest"))

        organization_id = d.pop("organization_id")

        skill_id = d.pop("skill_id")

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        skill_revision = cls(
            created_at=created_at,
            created_by=created_by,
            id=id,
            imported_from=imported_from,
            manifest=manifest,
            organization_id=organization_id,
            skill_id=skill_id,
            version=version,
            workspace_id=workspace_id,
        )

        return skill_revision
