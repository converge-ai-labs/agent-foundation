from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.mcp_auth_mode import MCPAuthMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="CreateMCPConnectionRequest")


@_attrs_define(repr=False)
class CreateMCPConnectionRequest:
    """
    Attributes:
        auth_mode (MCPAuthMode):
        endpoint_url (str):
        name (str):
        static_header_names (list[str] | Unset):
    """

    auth_mode: MCPAuthMode
    endpoint_url: str
    name: str
    static_header_names: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        auth_mode = self.auth_mode.value

        endpoint_url = self.endpoint_url

        name = self.name

        static_header_names: list[str] | Unset = UNSET
        if not isinstance(self.static_header_names, Unset):
            static_header_names = self.static_header_names

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "auth_mode": auth_mode,
                "endpoint_url": endpoint_url,
                "name": name,
            }
        )
        if static_header_names is not UNSET:
            field_dict["static_header_names"] = static_header_names

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        auth_mode = MCPAuthMode(d.pop("auth_mode"))

        endpoint_url = d.pop("endpoint_url")

        name = d.pop("name")

        static_header_names = cast(list[str], d.pop("static_header_names", UNSET))

        create_mcp_connection_request = cls(
            auth_mode=auth_mode,
            endpoint_url=endpoint_url,
            name=name,
            static_header_names=static_header_names,
        )

        return create_mcp_connection_request
