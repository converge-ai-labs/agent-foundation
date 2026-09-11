from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="Content")


@_attrs_define(repr=False)
class Content:
    """
    Attributes:
        media_type (None | str):
        value (Any):
    """

    media_type: str | None
    value: Any

    def to_dict(self) -> dict[str, Any]:
        media_type: str | None
        media_type = self.media_type

        value = self.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "media_type": media_type,
                "value": value,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)

        def _parse_media_type(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        media_type = _parse_media_type(d.pop("media_type"))

        value = d.pop("value")

        content = cls(
            media_type=media_type,
            value=value,
        )

        return content
