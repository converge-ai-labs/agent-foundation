from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="UrlBinarySource")


@_attrs_define(repr=False)
class UrlBinarySource:
    """
    Attributes:
        url (str):
        type_ (Literal['url'] | Unset):
    """

    url: str
    type_: Literal["url"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        url = self.url

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "url": url,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        url = d.pop("url")

        type_ = cast(Literal["url"] | Unset, d.pop("type", UNSET))
        if type_ != "url" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'url', got '{type_}'")

        url_binary_source = cls(
            url=url,
            type_=type_,
        )

        return url_binary_source
