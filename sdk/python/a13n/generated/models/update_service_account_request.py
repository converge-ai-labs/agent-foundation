from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.update_service_account_request_role import UpdateServiceAccountRequestRole
from ..models.update_service_account_request_status import UpdateServiceAccountRequestStatus
from ..types import UNSET, Unset

T = TypeVar("T", bound="UpdateServiceAccountRequest")


@_attrs_define(repr=False)
class UpdateServiceAccountRequest:
    """
    Attributes:
        expected_version (int):
        name (str):
        role (UpdateServiceAccountRequestRole):
        status (UpdateServiceAccountRequestStatus):
        description (None | str | Unset):
    """

    expected_version: int
    name: str
    role: UpdateServiceAccountRequestRole
    status: UpdateServiceAccountRequestStatus
    description: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        name = self.name

        role = self.role.value

        status = self.status.value

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "name": name,
                "role": role,
                "status": status,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        name = d.pop("name")

        role = UpdateServiceAccountRequestRole(d.pop("role"))

        status = UpdateServiceAccountRequestStatus(d.pop("status"))

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        update_service_account_request = cls(
            expected_version=expected_version,
            name=name,
            role=role,
            status=status,
            description=description,
        )

        return update_service_account_request
