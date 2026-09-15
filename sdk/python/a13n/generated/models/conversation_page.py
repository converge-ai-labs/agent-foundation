from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.conversation_candidate import ConversationCandidate


T = TypeVar("T", bound="ConversationPage")


@_attrs_define(repr=False)
class ConversationPage:
    """
    Attributes:
        items (list[ConversationCandidate]):
        cursor (None | str | Unset):
    """

    items: list[ConversationCandidate]
    cursor: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        cursor: str | Unset | None
        if isinstance(self.cursor, Unset):
            cursor = UNSET
        else:
            cursor = self.cursor

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
            }
        )
        if cursor is not UNSET:
            field_dict["cursor"] = cursor

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.conversation_candidate import ConversationCandidate

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = ConversationCandidate.from_dict(items_item_data)

            items.append(items_item)

        def _parse_cursor(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        cursor = _parse_cursor(d.pop("cursor", UNSET))

        conversation_page = cls(
            items=items,
            cursor=cursor,
        )

        return conversation_page
