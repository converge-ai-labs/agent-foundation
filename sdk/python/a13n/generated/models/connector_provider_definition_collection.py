from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector_provider_definition import ConnectorProviderDefinition


T = TypeVar("T", bound="ConnectorProviderDefinitionCollection")


@_attrs_define(repr=False)
class ConnectorProviderDefinitionCollection:
    """
    Attributes:
        items (list[ConnectorProviderDefinition]):
        next_cursor (None | Unset):
    """

    items: list[ConnectorProviderDefinition]
    next_cursor: Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        next_cursor = self.next_cursor

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
            }
        )
        if next_cursor is not UNSET:
            field_dict["next_cursor"] = next_cursor

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_provider_definition import ConnectorProviderDefinition

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = ConnectorProviderDefinition.from_dict(items_item_data)

            items.append(items_item)

        next_cursor = d.pop("next_cursor", UNSET)

        connector_provider_definition_collection = cls(
            items=items,
            next_cursor=next_cursor,
        )

        return connector_provider_definition_collection
