from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="SecretRequirement")


@_attrs_define(repr=False)
class SecretRequirement:
    """
    Attributes:
        key (str):
        description (None | str | Unset):
        required (bool | Unset):
    """

    key: str
    description: str | Unset | None = UNSET
    required: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        key = self.key

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        required = self.required

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "key": key,
            }
        )
        if description is not UNSET:
            field_dict["description"] = description
        if required is not UNSET:
            field_dict["required"] = required

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        key = d.pop("key")

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        required = d.pop("required", UNSET)

        secret_requirement = cls(
            key=key,
            description=description,
            required=required,
        )

        return secret_requirement
