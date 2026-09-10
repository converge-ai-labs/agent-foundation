from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.change_role_request_role import ChangeRoleRequestRole

T = TypeVar("T", bound="ChangeRoleRequest")


@_attrs_define(repr=False)
class ChangeRoleRequest:
    """
    Attributes:
        role (ChangeRoleRequestRole):
    """

    role: ChangeRoleRequestRole

    def to_dict(self) -> dict[str, Any]:
        role = self.role.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "role": role,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        role = ChangeRoleRequestRole(d.pop("role"))

        change_role_request = cls(
            role=role,
        )

        return change_role_request
