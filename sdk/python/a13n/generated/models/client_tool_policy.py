from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="ClientToolPolicy")


@_attrs_define(repr=False)
class ClientToolPolicy:
    """
    Attributes:
        name (str):
        required (bool | Unset):
    """

    name: str
    required: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        name = self.name

        required = self.required

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
            }
        )
        if required is not UNSET:
            field_dict["required"] = required

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        name = d.pop("name")

        required = d.pop("required", UNSET)

        client_tool_policy = cls(
            name=name,
            required=required,
        )

        return client_tool_policy
