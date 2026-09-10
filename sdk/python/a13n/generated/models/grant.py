from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.grant_resource_type import GrantResourceType
from ..models.grant_role_key import GrantRoleKey

T = TypeVar("T", bound="Grant")


@_attrs_define(repr=False)
class Grant:
    """
    Attributes:
        resource_id (str):
        resource_type (GrantResourceType):
        role_key (GrantRoleKey):
    """

    resource_id: str
    resource_type: GrantResourceType
    role_key: GrantRoleKey

    def to_dict(self) -> dict[str, Any]:
        resource_id = self.resource_id

        resource_type = self.resource_type.value

        role_key = self.role_key.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "resource_id": resource_id,
                "resource_type": resource_type,
                "role_key": role_key,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        resource_id = d.pop("resource_id")

        resource_type = GrantResourceType(d.pop("resource_type"))

        role_key = GrantRoleKey(d.pop("role_key"))

        grant = cls(
            resource_id=resource_id,
            resource_type=resource_type,
            role_key=role_key,
        )

        return grant
