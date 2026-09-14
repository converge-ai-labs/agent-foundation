from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.environment_template_labels import EnvironmentTemplateLabels


T = TypeVar("T", bound="EnvironmentTemplate")


@_attrs_define(repr=False)
class EnvironmentTemplate:
    """
    Attributes:
        archived_at (datetime.datetime | None):
        created_at (datetime.datetime):
        current_revision_id (str):
        description (None | str):
        id (str):
        name (str):
        organization_id (str):
        updated_at (datetime.datetime):
        version (int):
        workspace_id (None | str):
        labels (EnvironmentTemplateLabels | Unset):
    """

    archived_at: datetime.datetime | None
    created_at: datetime.datetime
    current_revision_id: str
    description: str | None
    id: str
    name: str
    organization_id: str
    updated_at: datetime.datetime
    version: int
    workspace_id: str | None
    labels: EnvironmentTemplateLabels | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        archived_at: str | None
        if isinstance(self.archived_at, datetime.datetime):
            archived_at = self.archived_at.isoformat()
        else:
            archived_at = self.archived_at

        created_at = self.created_at.isoformat()

        current_revision_id = self.current_revision_id

        description: str | None
        description = self.description

        id = self.id

        name = self.name

        organization_id = self.organization_id

        updated_at = self.updated_at.isoformat()

        version = self.version

        workspace_id: str | None
        workspace_id = self.workspace_id

        labels: dict[str, Any] | Unset = UNSET
        if not isinstance(self.labels, Unset):
            labels = self.labels.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "archived_at": archived_at,
                "created_at": created_at,
                "current_revision_id": current_revision_id,
                "description": description,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "updated_at": updated_at,
                "version": version,
                "workspace_id": workspace_id,
            }
        )
        if labels is not UNSET:
            field_dict["labels"] = labels

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.environment_template_labels import EnvironmentTemplateLabels

        d = dict(src_dict)

        def _parse_archived_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                archived_at_type_0 = datetime.datetime.fromisoformat(data)

                return archived_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        archived_at = _parse_archived_at(d.pop("archived_at"))

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        current_revision_id = d.pop("current_revision_id")

        def _parse_description(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        description = _parse_description(d.pop("description"))

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        version = d.pop("version")

        def _parse_workspace_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        workspace_id = _parse_workspace_id(d.pop("workspace_id"))

        _labels = d.pop("labels", UNSET)
        labels: EnvironmentTemplateLabels | Unset
        if isinstance(_labels, Unset):
            labels = UNSET
        else:
            labels = EnvironmentTemplateLabels.from_dict(_labels)

        environment_template = cls(
            archived_at=archived_at,
            created_at=created_at,
            current_revision_id=current_revision_id,
            description=description,
            id=id,
            name=name,
            organization_id=organization_id,
            updated_at=updated_at,
            version=version,
            workspace_id=workspace_id,
            labels=labels,
        )

        return environment_template
