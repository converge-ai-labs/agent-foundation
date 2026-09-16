from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="BotCheckRequest")


@_attrs_define(repr=False)
class BotCheckRequest:
    """
    Attributes:
        expected_version (int):
        conversation_id (None | str | Unset):
    """

    expected_version: int
    conversation_id: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        expected_version = self.expected_version

        conversation_id: str | Unset | None
        if isinstance(self.conversation_id, Unset):
            conversation_id = UNSET
        else:
            conversation_id = self.conversation_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
            }
        )
        if conversation_id is not UNSET:
            field_dict["conversation_id"] = conversation_id

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        def _parse_conversation_id(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        conversation_id = _parse_conversation_id(d.pop("conversation_id", UNSET))

        bot_check_request = cls(
            expected_version=expected_version,
            conversation_id=conversation_id,
        )

        return bot_check_request
