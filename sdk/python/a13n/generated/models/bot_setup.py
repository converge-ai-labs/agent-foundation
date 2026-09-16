from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="BotSetup")


@_attrs_define(repr=False)
class BotSetup:
    """
    Attributes:
        account_id (str):
        event_path (str):
        event_url (None | str):
    """

    account_id: str
    event_path: str
    event_url: str | None

    def to_dict(self) -> dict[str, Any]:
        account_id = self.account_id

        event_path = self.event_path

        event_url: str | None
        event_url = self.event_url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "account_id": account_id,
                "event_path": event_path,
                "event_url": event_url,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        account_id = d.pop("account_id")

        event_path = d.pop("event_path")

        def _parse_event_url(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        event_url = _parse_event_url(d.pop("event_url"))

        bot_setup = cls(
            account_id=account_id,
            event_path=event_path,
            event_url=event_url,
        )

        return bot_setup
