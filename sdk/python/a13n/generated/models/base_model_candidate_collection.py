from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.base_model_candidate import BaseModelCandidate


T = TypeVar("T", bound="BaseModelCandidateCollection")


@_attrs_define(repr=False)
class BaseModelCandidateCollection:
    """
    Attributes:
        items (list[BaseModelCandidate]):
    """

    items: list[BaseModelCandidate]

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.base_model_candidate import BaseModelCandidate

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = BaseModelCandidate.from_dict(items_item_data)

            items.append(items_item)

        base_model_candidate_collection = cls(
            items=items,
        )

        return base_model_candidate_collection
