from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.run_lineage_entry import RunLineageEntry


T = TypeVar("T", bound="RunLineage")


@_attrs_define(repr=False)
class RunLineage:
    """
    Attributes:
        head_run_id (str):
        items (list[RunLineageEntry]):
    """

    head_run_id: str
    items: list[RunLineageEntry]

    def to_dict(self) -> dict[str, Any]:
        head_run_id = self.head_run_id

        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "head_run_id": head_run_id,
                "items": items,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.run_lineage_entry import RunLineageEntry

        d = dict(src_dict)
        head_run_id = d.pop("head_run_id")

        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = RunLineageEntry.from_dict(items_item_data)

            items.append(items_item)

        run_lineage = cls(
            head_run_id=head_run_id,
            items=items,
        )

        return run_lineage
