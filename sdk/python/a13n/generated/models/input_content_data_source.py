from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="InputContentDataSource")


@_attrs_define(repr=False)
class InputContentDataSource:
    """Inline base64-encoded source.

    Attributes:
        mime_type (str):
        value (str):
        type_ (Literal['data'] | Unset):
    """

    mime_type: str
    value: str
    type_: Literal["data"] | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        mime_type = self.mime_type

        value = self.value

        type_ = self.type_

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "mimeType": mime_type,
                "value": value,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        mime_type = d.pop("mimeType")

        value = d.pop("value")

        type_ = cast(Literal["data"] | Unset, d.pop("type", UNSET))
        if type_ != "data" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'data', got '{type_}'")

        input_content_data_source = cls(
            mime_type=mime_type,
            value=value,
            type_=type_,
        )

        input_content_data_source.additional_properties = d
        return input_content_data_source

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
