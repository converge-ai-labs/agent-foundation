from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.lifecycle_event import LifecycleEvent


T = TypeVar("T", bound="WorkspaceEventPage")


@_attrs_define(repr=False)
class WorkspaceEventPage:
    """
    Attributes:
        high_watermark (str):
        items (list[LifecycleEvent]):
        next_cursor (None | str):
        retained_floor (str):
    """

    high_watermark: str
    items: list[LifecycleEvent]
    next_cursor: str | None
    retained_floor: str

    def to_dict(self) -> dict[str, Any]:
        high_watermark = self.high_watermark

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        next_cursor: str | None
        next_cursor = self.next_cursor

        retained_floor = self.retained_floor

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "high_watermark": high_watermark,
                "items": items,
                "next_cursor": next_cursor,
                "retained_floor": retained_floor,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.lifecycle_event import LifecycleEvent

        d = dict(src_dict)
        high_watermark = d.pop("high_watermark")

        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = LifecycleEvent.from_dict(items_item_data)

            items.append(items_item)

        def _parse_next_cursor(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor"))

        retained_floor = d.pop("retained_floor")

        workspace_event_page = cls(
            high_watermark=high_watermark,
            items=items,
            next_cursor=next_cursor,
            retained_floor=retained_floor,
        )

        return workspace_event_page
