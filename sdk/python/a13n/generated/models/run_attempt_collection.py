from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.run_attempt_resource import RunAttemptResource


T = TypeVar("T", bound="RunAttemptCollection")


@_attrs_define(repr=False)
class RunAttemptCollection:
    """
    Attributes:
        items (list[RunAttemptResource]):
        next_cursor (None | str):
    """

    items: list[RunAttemptResource]
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
        from ..models.run_attempt_resource import RunAttemptResource

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = RunAttemptResource.from_dict(items_item_data)

            items.append(items_item)

        def _parse_next_cursor(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor"))

        run_attempt_collection = cls(
            items=items,
            next_cursor=next_cursor,
        )

        return run_attempt_collection
