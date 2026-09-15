from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.bot_test import BotTest


T = TypeVar("T", bound="BotTestHistory")


@_attrs_define(repr=False)
class BotTestHistory:
    """
    Attributes:
        latest (BotTest | None):
    """

    latest: BotTest | None

    def to_dict(self) -> dict[str, Any]:
        from ..models.bot_test import BotTest

        latest: dict[str, Any] | None
        if isinstance(self.latest, BotTest):
            latest = self.latest.to_dict()
        else:
            latest = self.latest

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "latest": latest,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.bot_test import BotTest

        d = dict(src_dict)

        def _parse_latest(data: object) -> BotTest | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                latest_type_0 = BotTest.from_dict(data)

                return latest_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(BotTest | None, data)

        latest = _parse_latest(d.pop("latest"))

        bot_test_history = cls(
            latest=latest,
        )

        return bot_test_history
