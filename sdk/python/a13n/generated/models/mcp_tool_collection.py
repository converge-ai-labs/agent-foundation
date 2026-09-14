from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.mcp_tool import MCPTool


T = TypeVar("T", bound="MCPToolCollection")


@_attrs_define(repr=False)
class MCPToolCollection:
    """
    Attributes:
        items (list[MCPTool]):
    """

    items: list[MCPTool]

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
        from ..models.mcp_tool import MCPTool

        d = dict(src_dict)
        items = []
        _items = d.pop("items")
        for items_item_data in _items:
            items_item = MCPTool.from_dict(items_item_data)

            items.append(items_item)

        mcp_tool_collection = cls(
            items=items,
        )

        return mcp_tool_collection
