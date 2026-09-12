from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.mcp_server_auth_mode import MCPServerAuthMode
from ..models.mcp_server_origin import MCPServerOrigin
from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPServer")


@_attrs_define(repr=False)
class MCPServer:
    """
    Attributes:
        auth_mode (MCPServerAuthMode):
        description (str):
        endpoint_url (str):
        key (str):
        name (str):
        documentation_url (None | str | Unset):
        logo_url (None | str | Unset):
        origin (MCPServerOrigin | Unset):
        requirements (str | Unset):
        static_header_names (list[str] | Unset):
    """

    auth_mode: MCPServerAuthMode
    description: str
    endpoint_url: str
    key: str
    name: str
    documentation_url: str | Unset | None = UNSET
    logo_url: str | Unset | None = UNSET
    origin: MCPServerOrigin | Unset = UNSET
    requirements: str | Unset = UNSET
    static_header_names: list[str] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        auth_mode = self.auth_mode.value

        description = self.description

        endpoint_url = self.endpoint_url

        key = self.key

        name = self.name

        documentation_url: str | Unset | None
        if isinstance(self.documentation_url, Unset):
            documentation_url = UNSET
        else:
            documentation_url = self.documentation_url

        logo_url: str | Unset | None
        if isinstance(self.logo_url, Unset):
            logo_url = UNSET
        else:
            logo_url = self.logo_url

        origin: str | Unset = UNSET
        if not isinstance(self.origin, Unset):
            origin = self.origin.value

        requirements = self.requirements

        static_header_names: list[str] | Unset = UNSET
        if not isinstance(self.static_header_names, Unset):
            static_header_names = self.static_header_names

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "auth_mode": auth_mode,
                "description": description,
                "endpoint_url": endpoint_url,
                "key": key,
                "name": name,
            }
        )
        if documentation_url is not UNSET:
            field_dict["documentation_url"] = documentation_url
        if logo_url is not UNSET:
            field_dict["logo_url"] = logo_url
        if origin is not UNSET:
            field_dict["origin"] = origin
        if requirements is not UNSET:
            field_dict["requirements"] = requirements
        if static_header_names is not UNSET:
            field_dict["static_header_names"] = static_header_names

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        auth_mode = MCPServerAuthMode(d.pop("auth_mode"))

        description = d.pop("description")

        endpoint_url = d.pop("endpoint_url")

        key = d.pop("key")

        name = d.pop("name")

        def _parse_documentation_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        documentation_url = _parse_documentation_url(d.pop("documentation_url", UNSET))

        def _parse_logo_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        logo_url = _parse_logo_url(d.pop("logo_url", UNSET))

        _origin = d.pop("origin", UNSET)
        origin: MCPServerOrigin | Unset
        if isinstance(_origin, Unset):
            origin = UNSET
        else:
            origin = MCPServerOrigin(_origin)

        requirements = d.pop("requirements", UNSET)

        static_header_names = cast(list[str], d.pop("static_header_names", UNSET))

        mcp_server = cls(
            auth_mode=auth_mode,
            description=description,
            endpoint_url=endpoint_url,
            key=key,
            name=name,
            documentation_url=documentation_url,
            logo_url=logo_url,
            origin=origin,
            requirements=requirements,
            static_header_names=static_header_names,
        )

        return mcp_server
