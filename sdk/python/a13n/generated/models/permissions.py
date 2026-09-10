from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="Permissions")


@_attrs_define(repr=False)
class Permissions:
    """
    Attributes:
        actions (list[str]):
        organization_admin (bool):
    """

    actions: list[str]
    organization_admin: bool

    def to_dict(self) -> dict[str, Any]:
        actions = self.actions

        organization_admin = self.organization_admin

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "actions": actions,
                "organization_admin": organization_admin,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        actions = cast(list[str], d.pop("actions"))

        organization_admin = d.pop("organization_admin")

        permissions = cls(
            actions=actions,
            organization_admin=organization_admin,
        )

        return permissions
