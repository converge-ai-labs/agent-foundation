from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPClientMetadata")


@_attrs_define(repr=False)
class MCPClientMetadata:
    """
    Attributes:
        client_id (str):
        client_name (str):
        redirect_uris (list[str]):
        grant_types (list[str] | Unset):
        response_types (list[str] | Unset):
        token_endpoint_auth_method (str | Unset):
    """

    client_id: str
    client_name: str
    redirect_uris: list[str]
    grant_types: list[str] | Unset = UNSET
    response_types: list[str] | Unset = UNSET
    token_endpoint_auth_method: str | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        client_id = self.client_id

        client_name = self.client_name

        redirect_uris = self.redirect_uris

        grant_types: list[str] | Unset = UNSET
        if not isinstance(self.grant_types, Unset):
            grant_types = self.grant_types

        response_types: list[str] | Unset = UNSET
        if not isinstance(self.response_types, Unset):
            response_types = self.response_types

        token_endpoint_auth_method = self.token_endpoint_auth_method

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "client_id": client_id,
                "client_name": client_name,
                "redirect_uris": redirect_uris,
            }
        )
        if grant_types is not UNSET:
            field_dict["grant_types"] = grant_types
        if response_types is not UNSET:
            field_dict["response_types"] = response_types
        if token_endpoint_auth_method is not UNSET:
            field_dict["token_endpoint_auth_method"] = token_endpoint_auth_method

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        client_id = d.pop("client_id")

        client_name = d.pop("client_name")

        redirect_uris = cast(list[str], d.pop("redirect_uris"))

        grant_types = cast(list[str], d.pop("grant_types", UNSET))

        response_types = cast(list[str], d.pop("response_types", UNSET))

        token_endpoint_auth_method = d.pop("token_endpoint_auth_method", UNSET)

        mcp_client_metadata = cls(
            client_id=client_id,
            client_name=client_name,
            redirect_uris=redirect_uris,
            grant_types=grant_types,
            response_types=response_types,
            token_endpoint_auth_method=token_endpoint_auth_method,
        )

        return mcp_client_metadata
