from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.create_service_account_request_role import CreateServiceAccountRequestRole
from ..types import UNSET, Unset

T = TypeVar("T", bound="CreateServiceAccountRequest")


@_attrs_define(repr=False)
class CreateServiceAccountRequest:
    """
    Attributes:
        name (str):
        role (CreateServiceAccountRequestRole):
        description (None | str | Unset):
    """

    name: str
    role: CreateServiceAccountRequestRole
    description: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        role = self.role.value

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
                "role": role,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        role = CreateServiceAccountRequestRole(d.pop("role"))

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        create_service_account_request = cls(
            name=name,
            role=role,
            description=description,
        )

        return create_service_account_request
