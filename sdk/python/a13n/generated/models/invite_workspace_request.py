from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.invite_workspace_request_role import InviteWorkspaceRequestRole

T = TypeVar("T", bound="InviteWorkspaceRequest")


@_attrs_define(repr=False)
class InviteWorkspaceRequest:
    """
    Attributes:
        email (str):
        role (InviteWorkspaceRequestRole):
    """

    email: str
    role: InviteWorkspaceRequestRole

    def to_dict(self) -> dict[str, Any]:
        email = self.email

        role = self.role.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "email": email,
                "role": role,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        email = d.pop("email")

        role = InviteWorkspaceRequestRole(d.pop("role"))

        invite_workspace_request = cls(
            email=email,
            role=role,
        )

        return invite_workspace_request
