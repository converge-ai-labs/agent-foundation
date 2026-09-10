from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="ApiKey")


@_attrs_define(repr=False)
class ApiKey:
    """
    Attributes:
        boundary_id (str):
        boundary_type (str):
        created_at (datetime.datetime):
        expires_at (datetime.datetime | None):
        id (str):
        name (str):
        organization_id (str):
        principal_id (str):
        principal_type (str):
        revoked_at (datetime.datetime | None):
        updated_at (datetime.datetime):
    """

    boundary_id: str
    boundary_type: str
    created_at: datetime.datetime
    expires_at: datetime.datetime | None
    id: str
    name: str
    organization_id: str
    principal_id: str
    principal_type: str
    revoked_at: datetime.datetime | None
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        boundary_id = self.boundary_id

        boundary_type = self.boundary_type

        created_at = self.created_at.isoformat()

        expires_at: str | None
        if isinstance(self.expires_at, datetime.datetime):
            expires_at = self.expires_at.isoformat()
        else:
            expires_at = self.expires_at

        id = self.id

        name = self.name

        organization_id = self.organization_id

        principal_id = self.principal_id

        principal_type = self.principal_type

        revoked_at: str | None
        if isinstance(self.revoked_at, datetime.datetime):
            revoked_at = self.revoked_at.isoformat()
        else:
            revoked_at = self.revoked_at

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "boundary_id": boundary_id,
                "boundary_type": boundary_type,
                "created_at": created_at,
                "expires_at": expires_at,
                "id": id,
                "name": name,
                "organization_id": organization_id,
                "principal_id": principal_id,
                "principal_type": principal_type,
                "revoked_at": revoked_at,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        boundary_id = d.pop("boundary_id")

        boundary_type = d.pop("boundary_type")

        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        def _parse_expires_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                expires_at_type_0 = datetime.datetime.fromisoformat(data)

                return expires_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        expires_at = _parse_expires_at(d.pop("expires_at"))

        id = d.pop("id")

        name = d.pop("name")

        organization_id = d.pop("organization_id")

        principal_id = d.pop("principal_id")

        principal_type = d.pop("principal_type")

        def _parse_revoked_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                revoked_at_type_0 = datetime.datetime.fromisoformat(data)

                return revoked_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        revoked_at = _parse_revoked_at(d.pop("revoked_at"))

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        api_key = cls(
            boundary_id=boundary_id,
            boundary_type=boundary_type,
            created_at=created_at,
            expires_at=expires_at,
            id=id,
            name=name,
            organization_id=organization_id,
            principal_id=principal_id,
            principal_type=principal_type,
            revoked_at=revoked_at,
            updated_at=updated_at,
        )

        api_key.additional_properties = d
        return api_key

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
