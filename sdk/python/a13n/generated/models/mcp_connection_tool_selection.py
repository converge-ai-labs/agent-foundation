from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPConnectionToolSelection")


@_attrs_define(repr=False)
class MCPConnectionToolSelection:
    """
    Attributes:
        mcp_connection_id (str):
        defer_loading (bool | Unset):
        tools (list[str] | None | Unset):
    """

    mcp_connection_id: str
    defer_loading: bool | Unset = UNSET
    tools: list[str] | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        mcp_connection_id = self.mcp_connection_id

        defer_loading = self.defer_loading

        tools: list[str] | Unset | None
        if isinstance(self.tools, Unset):
            tools = UNSET
        elif isinstance(self.tools, list):
            tools = self.tools

        else:
            tools = self.tools

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "mcp_connection_id": mcp_connection_id,
            }
        )
        if defer_loading is not UNSET:
            field_dict["defer_loading"] = defer_loading
        if tools is not UNSET:
            field_dict["tools"] = tools

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        mcp_connection_id = d.pop("mcp_connection_id")

        defer_loading = d.pop("defer_loading", UNSET)

        def _parse_tools(data: object) -> list[str] | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, list):
                    raise TypeError()
                tools_type_0 = cast(list[str], data)

                return tools_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(list[str] | Unset | None, data)

        tools = _parse_tools(d.pop("tools", UNSET))

        mcp_connection_tool_selection = cls(
            mcp_connection_id=mcp_connection_id,
            defer_loading=defer_loading,
            tools=tools,
        )

        return mcp_connection_tool_selection
