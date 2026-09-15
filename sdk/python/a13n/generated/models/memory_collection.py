from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.memory import Memory
    from ..models.memory_pagination import MemoryPagination


T = TypeVar("T", bound="MemoryCollection")


@_attrs_define(repr=False)
class MemoryCollection:
    """
    Attributes:
        items (list[Memory]):
        pagination (MemoryPagination | None | Unset): Native pagination when available. Null means a bounded result, not
            a complete collection.
    """

    items: list[Memory]
    pagination: MemoryPagination | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.memory_pagination import MemoryPagination

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        pagination: dict[str, Any] | Unset | None
        if isinstance(self.pagination, Unset):
            pagination = UNSET
        elif isinstance(self.pagination, MemoryPagination):
            pagination = self.pagination.to_dict()
        else:
            pagination = self.pagination

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
            }
        )
        if pagination is not UNSET:
            field_dict["pagination"] = pagination

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.memory import Memory
        from ..models.memory_pagination import MemoryPagination

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = Memory.from_dict(items_item_data)

            items.append(items_item)

        def _parse_pagination(data: object) -> MemoryPagination | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                pagination_type_0 = MemoryPagination.from_dict(data)

                return pagination_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MemoryPagination | Unset | None, data)

        pagination = _parse_pagination(d.pop("pagination", UNSET))

        memory_collection = cls(
            items=items,
            pagination=pagination,
        )

        return memory_collection
