from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

T = TypeVar("T", bound="BinaryInputContent")


@_attrs_define(repr=False)
class BinaryInputContent:
    """A deprecated binary payload reference in a multimodal user message.

    Attributes:
        mime_type (str):
        data (None | str | Unset):
        filename (None | str | Unset):
        id (None | str | Unset):
        type_ (Literal['binary'] | Unset):
        url (None | str | Unset):
    """

    mime_type: str
    data: str | Unset | None = UNSET
    filename: str | Unset | None = UNSET
    id: str | Unset | None = UNSET
    type_: Literal["binary"] | Unset = UNSET
    url: str | Unset | None = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)

    def to_dict(self) -> dict[str, Any]:
        mime_type = self.mime_type

        data: str | Unset | None
        if isinstance(self.data, Unset):
            data = UNSET
        else:
            data = self.data

        filename: str | Unset | None
        if isinstance(self.filename, Unset):
            filename = UNSET
        else:
            filename = self.filename

        id: str | Unset | None
        if isinstance(self.id, Unset):
            id = UNSET
        else:
            id = self.id

        type_ = self.type_

        url: str | Unset | None
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update(
            {
                "mimeType": mime_type,
            }
        )
        if data is not UNSET:
            field_dict["data"] = data
        if filename is not UNSET:
            field_dict["filename"] = filename
        if id is not UNSET:
            field_dict["id"] = id
        if type_ is not UNSET:
            field_dict["type"] = type_
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        mime_type = d.pop("mimeType")

        def _parse_data(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        data = _parse_data(d.pop("data", UNSET))

        def _parse_filename(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        filename = _parse_filename(d.pop("filename", UNSET))

        def _parse_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        id = _parse_id(d.pop("id", UNSET))

        type_ = cast(Literal["binary"] | Unset, d.pop("type", UNSET))
        if type_ != "binary" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'binary', got '{type_}'")

        def _parse_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        url = _parse_url(d.pop("url", UNSET))

        binary_input_content = cls(
            mime_type=mime_type,
            data=data,
            filename=filename,
            id=id,
            type_=type_,
            url=url,
        )

        binary_input_content.additional_properties = d
        return binary_input_content

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
