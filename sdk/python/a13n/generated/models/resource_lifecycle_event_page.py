from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.resource_lifecycle_event_page_resource_type import ResourceLifecycleEventPageResourceType

if TYPE_CHECKING:
    from ..models.lifecycle_event import LifecycleEvent


T = TypeVar("T", bound="ResourceLifecycleEventPage")


@_attrs_define(repr=False)
class ResourceLifecycleEventPage:
    """
    Attributes:
        high_watermark_resource_seq (int):
        items (list[LifecycleEvent]):
        next_resource_seq (int):
        resource_id (str):
        resource_type (ResourceLifecycleEventPageResourceType):
        retained_resource_seq_floor (int):
    """

    high_watermark_resource_seq: int
    items: list[LifecycleEvent]
    next_resource_seq: int
    resource_id: str
    resource_type: ResourceLifecycleEventPageResourceType
    retained_resource_seq_floor: int

    def to_dict(self) -> dict[str, Any]:
        high_watermark_resource_seq = self.high_watermark_resource_seq

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        next_resource_seq = self.next_resource_seq

        resource_id = self.resource_id

        resource_type = self.resource_type.value

        retained_resource_seq_floor = self.retained_resource_seq_floor

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "high_watermark_resource_seq": high_watermark_resource_seq,
                "items": items,
                "next_resource_seq": next_resource_seq,
                "resource_id": resource_id,
                "resource_type": resource_type,
                "retained_resource_seq_floor": retained_resource_seq_floor,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.lifecycle_event import LifecycleEvent

        d = dict(src_dict)
        high_watermark_resource_seq = d.pop("high_watermark_resource_seq")

        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = LifecycleEvent.from_dict(items_item_data)

            items.append(items_item)

        next_resource_seq = d.pop("next_resource_seq")

        resource_id = d.pop("resource_id")

        resource_type = ResourceLifecycleEventPageResourceType(d.pop("resource_type"))

        retained_resource_seq_floor = d.pop("retained_resource_seq_floor")

        resource_lifecycle_event_page = cls(
            high_watermark_resource_seq=high_watermark_resource_seq,
            items=items,
            next_resource_seq=next_resource_seq,
            resource_id=resource_id,
            resource_type=resource_type,
            retained_resource_seq_floor=retained_resource_seq_floor,
        )

        return resource_lifecycle_event_page
