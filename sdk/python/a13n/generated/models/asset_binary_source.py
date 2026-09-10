from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="AssetBinarySource")


@_attrs_define(repr=False)
class AssetBinarySource:
    """
    Attributes:
        asset_id (str):
        type_ (Literal['asset'] | Unset):
    """

    asset_id: str
    type_: Literal["asset"] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        asset_id = self.asset_id

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "asset_id": asset_id,
            }
        )
        if type_ is not UNSET:
            field_dict["type"] = type_

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        asset_id = d.pop("asset_id")

        type_ = cast(Literal["asset"] | Unset, d.pop("type", UNSET))
        if type_ != "asset" and not isinstance(type_, Unset):
            raise ValueError(f"type must match const 'asset', got '{type_}'")

        asset_binary_source = cls(
            asset_id=asset_id,
            type_=type_,
        )

        return asset_binary_source
