from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

from ..models.mcp_source_auth_mode import MCPSourceAuthMode
from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPSource")


@_attrs_define(repr=False)
class MCPSource:
    """
    Attributes:
        auth_mode (MCPSourceAuthMode):
        endpoint_url (str):
        kind (Literal['mcp']):
        static_header_names (list[str] | Unset):
    """

    auth_mode: MCPSourceAuthMode
    endpoint_url: str
    kind: Literal["mcp"]
    static_header_names: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        auth_mode = self.auth_mode.value

        endpoint_url = self.endpoint_url

        kind = self.kind

        static_header_names: list[str] | Unset = UNSET
        if not isinstance(self.static_header_names, Unset):
            static_header_names = self.static_header_names

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "auth_mode": auth_mode,
                "endpoint_url": endpoint_url,
                "kind": kind,
            }
        )
        if static_header_names is not UNSET:
            field_dict["static_header_names"] = static_header_names

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        auth_mode = MCPSourceAuthMode(d.pop("auth_mode"))

        endpoint_url = d.pop("endpoint_url")

        kind = cast(Literal["mcp"], d.pop("kind"))
        if kind != "mcp":
            raise ValueError(f"kind must match const 'mcp', got '{kind}'")

        static_header_names = cast(list[str], d.pop("static_header_names", UNSET))

        mcp_source = cls(
            auth_mode=auth_mode,
            endpoint_url=endpoint_url,
            kind=kind,
            static_header_names=static_header_names,
        )

        return mcp_source
