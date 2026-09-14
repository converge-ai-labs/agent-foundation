from __future__ import annotations

import datetime
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector import Connector


T = TypeVar("T", bound="ConnectorCollection")


@_attrs_define(repr=False)
class ConnectorCollection:
    """
    Attributes:
        items (list[Connector]):
        next_cursor (None | str | Unset):
        refreshed_at (datetime.datetime | None | Unset):
    """

    items: list[Connector]
    next_cursor: str | Unset | None = UNSET
    refreshed_at: datetime.datetime | Unset | None = UNSET

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

        refreshed_at: str | Unset | None
        if isinstance(self.refreshed_at, Unset):
            refreshed_at = UNSET
        elif isinstance(self.refreshed_at, datetime.datetime):
            refreshed_at = self.refreshed_at.isoformat()
        else:
            refreshed_at = self.refreshed_at

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
            }
        )
        if next_cursor is not UNSET:
            field_dict["next_cursor"] = next_cursor
        if refreshed_at is not UNSET:
            field_dict["refreshed_at"] = refreshed_at

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector import Connector

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = Connector.from_dict(items_item_data)

            items.append(items_item)

        def _parse_next_cursor(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor", UNSET))

        def _parse_refreshed_at(data: object) -> datetime.datetime | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                refreshed_at_type_0 = datetime.datetime.fromisoformat(data)

                return refreshed_at_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(datetime.datetime | Unset | None, data)

        refreshed_at = _parse_refreshed_at(d.pop("refreshed_at", UNSET))

        connector_collection = cls(
            items=items,
            next_cursor=next_cursor,
            refreshed_at=refreshed_at,
        )

        return connector_collection
