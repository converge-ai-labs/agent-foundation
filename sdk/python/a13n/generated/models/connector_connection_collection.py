from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector_connection import ConnectorConnection


T = TypeVar("T", bound="ConnectorConnectionCollection")


@_attrs_define(repr=False)
class ConnectorConnectionCollection:
    """
    Attributes:
        items (list[ConnectorConnection]):
        next_cursor (None | str | Unset):
    """

    items: list[ConnectorConnection]
    next_cursor: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        next_cursor: str | Unset | None
        if isinstance(self.next_cursor, Unset):
            next_cursor = UNSET
        else:
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
        from ..models.connector_connection import ConnectorConnection

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = ConnectorConnection.from_dict(items_item_data)

            items.append(items_item)

        def _parse_next_cursor(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor", UNSET))

        connector_connection_collection = cls(
            items=items,
            next_cursor=next_cursor,
        )

        return connector_connection_collection
