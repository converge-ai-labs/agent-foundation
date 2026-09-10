from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="RoleBinding")


@_attrs_define(repr=False)
class RoleBinding:
    """
    Attributes:
        created_at (datetime.datetime):
        id (str):
        organization_id (str):
        principal_id (str):
        principal_type (str):
        resource_id (str):
        resource_type (str):
        role_key (str):
        updated_at (datetime.datetime):
        workspace_id (None | str):
    """

    created_at: datetime.datetime
    id: str
    organization_id: str
    principal_id: str
    principal_type: str
    resource_id: str
    resource_type: str
    role_key: str
    updated_at: datetime.datetime
    workspace_id: str | None
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        id = self.id

        organization_id = self.organization_id

        principal_id = self.principal_id

        principal_type = self.principal_type

        resource_id = self.resource_id

        resource_type = self.resource_type

        role_key = self.role_key

        updated_at = self.updated_at.isoformat()

        workspace_id: str | None
        workspace_id = self.workspace_id

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "id": id,
                "organization_id": organization_id,
                "principal_id": principal_id,
                "principal_type": principal_type,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "role_key": role_key,
                "updated_at": updated_at,
                "workspace_id": workspace_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        id = d.pop("id")

        organization_id = d.pop("organization_id")

        principal_id = d.pop("principal_id")

        principal_type = d.pop("principal_type")

        resource_id = d.pop("resource_id")

        resource_type = d.pop("resource_type")

        role_key = d.pop("role_key")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        def _parse_workspace_id(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        workspace_id = _parse_workspace_id(d.pop("workspace_id"))

        role_binding = cls(
            created_at=created_at,
            id=id,
            organization_id=organization_id,
            principal_id=principal_id,
            principal_type=principal_type,
            resource_id=resource_id,
            resource_type=resource_type,
            role_key=role_key,
            updated_at=updated_at,
            workspace_id=workspace_id,
        )

        role_binding.additional_properties = d
        return role_binding

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
