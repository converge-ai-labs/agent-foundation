from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="InstallationInfo")


@_attrs_define(repr=False)
class InstallationInfo:
    """
    Attributes:
        app_id (str):
        bot_id (str):
        bot_name (str):
        enabled (bool):
        organization_id (str):
        organization_name (str):
        enterprise_id (None | str | Unset):
    """

    app_id: str
    bot_id: str
    bot_name: str
    enabled: bool
    organization_id: str
    organization_name: str
    enterprise_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        app_id = self.app_id

        bot_id = self.bot_id

        bot_name = self.bot_name

        enabled = self.enabled

        organization_id = self.organization_id

        organization_name = self.organization_name

        enterprise_id: str | Unset | None
        if isinstance(self.enterprise_id, Unset):
            enterprise_id = UNSET
        else:
            enterprise_id = self.enterprise_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "app_id": app_id,
                "bot_id": bot_id,
                "bot_name": bot_name,
                "enabled": enabled,
                "organization_id": organization_id,
                "organization_name": organization_name,
            }
        )
        if enterprise_id is not UNSET:
            field_dict["enterprise_id"] = enterprise_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        app_id = d.pop("app_id")

        bot_id = d.pop("bot_id")

        bot_name = d.pop("bot_name")

        enabled = d.pop("enabled")

        organization_id = d.pop("organization_id")

        organization_name = d.pop("organization_name")

        def _parse_enterprise_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        enterprise_id = _parse_enterprise_id(d.pop("enterprise_id", UNSET))

        installation_info = cls(
            app_id=app_id,
            bot_id=bot_id,
            bot_name=bot_name,
            enabled=enabled,
            organization_id=organization_id,
            organization_name=organization_name,
            enterprise_id=enterprise_id,
        )

        return installation_info
