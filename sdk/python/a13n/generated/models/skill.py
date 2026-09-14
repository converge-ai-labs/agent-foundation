from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.principal_ref import PrincipalRef
    from ..models.skill_labels import SkillLabels


T = TypeVar("T", bound="Skill")


@_attrs_define(repr=False)
class Skill:
    """
    Attributes:
        created_at (datetime.datetime):
        created_by (PrincipalRef):
        current_revision_id (str):
        deleted_at (datetime.datetime | None):
        id (str):
        key (str):
        name (str):
        organization_id (str):
        updated_at (datetime.datetime):
        updated_by (PrincipalRef):
        version (int):
        workspace_id (str):
        labels (SkillLabels | Unset):
    """

    created_at: datetime.datetime
    created_by: PrincipalRef
    current_revision_id: str
    deleted_at: datetime.datetime | None
    id: str
    key: str
    name: str
    organization_id: str
    updated_at: datetime.datetime
    updated_by: PrincipalRef
    version: int
    workspace_id: str
    labels: SkillLabels | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        created_by = self.created_by.to_dict()

        current_revision_id = self.current_revision_id

        deleted_at: str | None
        if isinstance(self.deleted_at, datetime.datetime):
            deleted_at = self.deleted_at.isoformat()
        else:
            deleted_at = self.deleted_at

        id = self.id

        key = self.key

        name = self.name

        organization_id = self.organization_id

        updated_at = self.updated_at.isoformat()

        updated_by = self.updated_by.to_dict()

        version = self.version

        workspace_id = self.workspace_id

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "created_at": created_at,
                "created_by": created_by,
                "current_revision_id": current_revision_id,
                "deleted_at": deleted_at,
                "id": id,
                "key": key,
                "name": name,
                "organization_id": organization_id,
                "updated_at": updated_at,
                "updated_by": updated_by,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if labels is not UNSET:
            field_dict["labels"] = labels

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.principal_ref import PrincipalRef
        from ..models.skill_labels import SkillLabels

        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        created_by = PrincipalRef.from_dict(d.pop("created_by"))

        current_revision_id = d.pop("current_revision_id")

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

        id = d.pop("id")

        key = d.pop("key")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        updated_by = PrincipalRef.from_dict(d.pop("updated_by"))

        version = d.pop("version")

        workspace_id = d.pop("workspace_id")

        _labels = d.pop("labels", UNSET)
        labels: SkillLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = SkillLabels.from_dict(_labels)

        skill = cls(
            created_at=created_at,
            created_by=created_by,
            current_revision_id=current_revision_id,
            deleted_at=deleted_at,
            id=id,
            key=key,
            name=name,
            organization_id=organization_id,
            updated_at=updated_at,
            updated_by=updated_by,
            version=version,
            workspace_id=workspace_id,
            labels=labels,
        )

        return skill
