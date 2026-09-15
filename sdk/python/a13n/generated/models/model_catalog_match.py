from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.model_catalog_match_source import ModelCatalogMatchSource
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.model_catalog_suggestion import ModelCatalogSuggestion


T = TypeVar("T", bound="ModelCatalogMatch")


@_attrs_define(repr=False)
class ModelCatalogMatch:
    """
    Attributes:
        source (ModelCatalogMatchSource):
        items (list[ModelCatalogSuggestion] | Unset):
    """

    source: ModelCatalogMatchSource
    items: list[ModelCatalogSuggestion] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        source = self.source.value

        items: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.items, Unset):
            items = []
            for items_item_data in self.items:
                items_item = items_item_data.to_dict()
                items.append(items_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "source": source,
            }
        )
        if items is not UNSET:
            field_dict["items"] = items

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_catalog_suggestion import ModelCatalogSuggestion

        d = dict(src_dict)
        source = ModelCatalogMatchSource(d.pop("source"))

        _items = d.pop("items", UNSET)
        items: list[ModelCatalogSuggestion] | Unset = UNSET
        if _items is not UNSET:
            items = []
            for items_item_data in _items:
                items_item = ModelCatalogSuggestion.from_dict(items_item_data)

                items.append(items_item)

        model_catalog_match = cls(
            source=source,
            items=items,
        )

        return model_catalog_match
