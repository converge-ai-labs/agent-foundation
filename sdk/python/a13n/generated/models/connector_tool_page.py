from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector_tool import ConnectorTool


T = TypeVar("T", bound="ConnectorToolPage")


@_attrs_define(repr=False)
class ConnectorToolPage:
    """
    Attributes:
        items (list[ConnectorTool]):
        provider_version (str):
        next_cursor (None | str | Unset):
    """

    items: list[ConnectorTool]
    provider_version: str
    next_cursor: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        items = []
        for items_item_data in self.items:
            items_item = items_item_data.to_dict()
            items.append(items_item)

        provider_version = self.provider_version

        next_cursor: str | Unset | None
        if isinstance(self.next_cursor, Unset):
            next_cursor = UNSET
        else:
            next_cursor = self.next_cursor

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "items": items,
                "provider_version": provider_version,
            }
        )
        if next_cursor is not UNSET:
            field_dict["next_cursor"] = next_cursor

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_tool import ConnectorTool

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = ConnectorTool.from_dict(items_item_data)

            items.append(items_item)

        provider_version = d.pop("provider_version")

        def _parse_next_cursor(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        next_cursor = _parse_next_cursor(d.pop("next_cursor", UNSET))

        connector_tool_page = cls(
            items=items,
            provider_version=provider_version,
            next_cursor=next_cursor,
        )

        return connector_tool_page
