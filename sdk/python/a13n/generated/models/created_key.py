from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.api_key import ApiKey


T = TypeVar("T", bound="CreatedKey")


@_attrs_define(repr=False)
class CreatedKey:
    """
    Attributes:
        bearer (str):
        key (ApiKey):
    """

    bearer: str
    key: ApiKey

    def to_dict(self) -> dict[str, Any]:
        bearer = self.bearer

        key = self.key.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "bearer": bearer,
                "key": key,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.api_key import ApiKey

        d = dict(src_dict)
        bearer = d.pop("bearer")

        key = ApiKey.from_dict(d.pop("key"))

        created_key = cls(
            bearer=bearer,
            key=key,
        )

        return created_key
