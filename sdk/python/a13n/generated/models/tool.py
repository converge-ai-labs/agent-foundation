from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="Tool")


@_attrs_define(repr=False)
class Tool:
    """A tool definition.

    Attributes:
        description (str):
        name (str):
        parameters (Any | None | Unset):
    """

    description: str
    name: str
    parameters: Any | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        description = self.description

        name = self.name

        parameters: Any | Unset | None
        if isinstance(self.parameters, Unset):
            parameters = UNSET
        else:
            parameters = self.parameters

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "description": description,
                "name": name,
            }
        )
        if parameters is not UNSET:
            field_dict["parameters"] = parameters

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        description = d.pop("description")

        name = d.pop("name")

        def _parse_parameters(data: object) -> Any | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(Any | Unset | None, data)

        parameters = _parse_parameters(d.pop("parameters", UNSET))

        tool = cls(
            description=description,
            name=name,
            parameters=parameters,
        )

        tool.additional_properties = d
        return tool

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
