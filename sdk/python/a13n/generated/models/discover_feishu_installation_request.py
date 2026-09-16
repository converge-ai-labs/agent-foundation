from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="DiscoverFeishuInstallationRequest")


@_attrs_define(repr=False)
class DiscoverFeishuInstallationRequest:
    """
    Attributes:
        app_id (str):
        app_secret (str):
    """

    app_id: str
    app_secret: str

    def to_dict(self) -> dict[str, Any]:
        app_id = self.app_id

        app_secret = self.app_secret

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app_id": app_id,
                "app_secret": app_secret,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app_id = d.pop("app_id")

        app_secret = d.pop("app_secret")

        discover_feishu_installation_request = cls(
            app_id=app_id,
            app_secret=app_secret,
        )

        return discover_feishu_installation_request
