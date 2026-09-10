from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="OrganizationPermissions")


@_attrs_define(repr=False)
class OrganizationPermissions:
    """
    Attributes:
        organization_admin (bool):
    """

    organization_admin: bool

    def to_dict(self) -> dict[str, Any]:
        organization_admin = self.organization_admin

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "organization_admin": organization_admin,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        organization_admin = d.pop("organization_admin")

        organization_permissions = cls(
            organization_admin=organization_admin,
        )

        return organization_permissions
