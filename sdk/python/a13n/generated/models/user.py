from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

T = TypeVar("T", bound="User")


@_attrs_define(repr=False)
class User:
    """
    Attributes:
        created_at (datetime.datetime):
        email (str):
        email_verified_at (datetime.datetime | None):
        id (str):
        image_url (None | str):
        name (str):
        status (str):
        updated_at (datetime.datetime):
    """

    created_at: datetime.datetime
    email: str
    email_verified_at: datetime.datetime | None
    id: str
    image_url: str | None
    name: str
    status: str
    updated_at: datetime.datetime
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        created_at = self.created_at.isoformat()

        email = self.email

        email_verified_at: str | None
        if isinstance(self.email_verified_at, datetime.datetime):
            email_verified_at = self.email_verified_at.isoformat()
        else:
            email_verified_at = self.email_verified_at

        id = self.id

        image_url: str | None
        image_url = self.image_url

        name = self.name

        status = self.status

        updated_at = self.updated_at.isoformat()

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "created_at": created_at,
                "email": email,
                "email_verified_at": email_verified_at,
                "id": id,
                "image_url": image_url,
                "name": name,
                "status": status,
                "updated_at": updated_at,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        created_at = datetime.datetime.fromisoformat(d.pop("created_at"))

        email = d.pop("email")

        def _parse_email_verified_at(data: object) -> datetime.datetime | None:
            if data is None:
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                email_verified_at_type_0 = datetime.datetime.fromisoformat(data)

                return email_verified_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | None, data)

        email_verified_at = _parse_email_verified_at(d.pop("email_verified_at"))

        id = d.pop("id")

        def _parse_image_url(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        image_url = _parse_image_url(d.pop("image_url"))

        name = d.pop("name")

        status = d.pop("status")

        updated_at = datetime.datetime.fromisoformat(d.pop("updated_at"))

        user = cls(
            created_at=created_at,
            email=email,
            email_verified_at=email_verified_at,
            id=id,
            image_url=image_url,
            name=name,
            status=status,
            updated_at=updated_at,
        )

        user.additional_properties = d
        return user

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
