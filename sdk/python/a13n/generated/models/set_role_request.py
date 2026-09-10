from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.set_role_request_role import SetRoleRequestRole

T = TypeVar("T", bound="SetRoleRequest")


@_attrs_define(repr=False)
class SetRoleRequest:
    """
    Attributes:
        principal_id (str):
        role (SetRoleRequestRole):
    """

    principal_id: str
    role: SetRoleRequestRole

    def to_dict(self) -> dict[str, Any]:
        principal_id = self.principal_id

        role = self.role.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "principal_id": principal_id,
                "role": role,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        principal_id = d.pop("principal_id")

        role = SetRoleRequestRole(d.pop("role"))

        set_role_request = cls(
            principal_id=principal_id,
            role=role,
        )

        return set_role_request
