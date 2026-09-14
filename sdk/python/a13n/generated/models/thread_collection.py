from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.thread_resource import ThreadResource


T = TypeVar("T", bound="ThreadCollection")


@_attrs_define(repr=False)
class ThreadCollection:
    """
    Attributes:
        items (list[ThreadResource]):
        next_cursor (None | str):
    """

    items: list[ThreadResource]
    next_cursor: str | None

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        next_cursor: str | None
        next_cursor = self.next_cursor

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
                "next_cursor": next_cursor,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.thread_resource import ThreadResource

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = ThreadResource.from_dict(items_item_data)

            items.append(items_item)

        def _parse_next_cursor(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor"))

        thread_collection = cls(
            items=items,
            next_cursor=next_cursor,
        )

        return thread_collection
