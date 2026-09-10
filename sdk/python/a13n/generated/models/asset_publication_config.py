from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="AssetPublicationConfig")


@_attrs_define(repr=False)
class AssetPublicationConfig:
    """
    Attributes:
        enabled (bool | Unset):
    """

    enabled: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        enabled = self.enabled

        field_dict: dict[str, Any] = {}

        field_dict.update({})
        if enabled is not UNSET:
            field_dict["enabled"] = enabled

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        enabled = d.pop("enabled", UNSET)

        asset_publication_config = cls(
            enabled=enabled,
        )

        return asset_publication_config
