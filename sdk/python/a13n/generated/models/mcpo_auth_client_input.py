from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.mcpo_auth_client_input_grant_type import MCPOAuthClientInputGrantType
from ..models.mcpo_auth_client_input_token_endpoint_auth_method import MCPOAuthClientInputTokenEndpointAuthMethod
from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPOAuthClientInput")


@_attrs_define(repr=False)
class MCPOAuthClientInput:
    """
    Attributes:
        client_id (str):
        issuer_url (str):
        token_endpoint_auth_method (MCPOAuthClientInputTokenEndpointAuthMethod):
        client_secret (None | str | Unset):
        grant_type (MCPOAuthClientInputGrantType | Unset):
        redirect_uri (None | str | Unset):
    """

    client_id: str
    issuer_url: str
    token_endpoint_auth_method: MCPOAuthClientInputTokenEndpointAuthMethod
    client_secret: str | Unset | None = UNSET
    grant_type: MCPOAuthClientInputGrantType | Unset = UNSET
    redirect_uri: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        client_id = self.client_id

        issuer_url = self.issuer_url

        token_endpoint_auth_method = self.token_endpoint_auth_method.value

        client_secret: str | Unset | None
        if isinstance(self.client_secret, Unset):
            client_secret = UNSET
        else:
            client_secret = self.client_secret

        grant_type: str | Unset = UNSET
        if not isinstance(self.grant_type, Unset):
            grant_type = self.grant_type.value

        redirect_uri: str | Unset | None
        if isinstance(self.redirect_uri, Unset):
            redirect_uri = UNSET
        else:
            redirect_uri = self.redirect_uri

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "client_id": client_id,
                "issuer_url": issuer_url,
                "token_endpoint_auth_method": token_endpoint_auth_method,
            }
        )
        if client_secret is not UNSET:
            field_dict["client_secret"] = client_secret
        if grant_type is not UNSET:
            field_dict["grant_type"] = grant_type
        if redirect_uri is not UNSET:
            field_dict["redirect_uri"] = redirect_uri

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        client_id = d.pop("client_id")

        issuer_url = d.pop("issuer_url")

        token_endpoint_auth_method = MCPOAuthClientInputTokenEndpointAuthMethod(d.pop("token_endpoint_auth_method"))

        def _parse_client_secret(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        client_secret = _parse_client_secret(d.pop("client_secret", UNSET))

        _grant_type = d.pop("grant_type", UNSET)
        grant_type: MCPOAuthClientInputGrantType | Unset
        if isinstance(_grant_type, Unset):
            grant_type = UNSET
        else:
            grant_type = MCPOAuthClientInputGrantType(_grant_type)

        def _parse_redirect_uri(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        redirect_uri = _parse_redirect_uri(d.pop("redirect_uri", UNSET))

        mcpo_auth_client_input = cls(
            client_id=client_id,
            issuer_url=issuer_url,
            token_endpoint_auth_method=token_endpoint_auth_method,
            client_secret=client_secret,
            grant_type=grant_type,
            redirect_uri=redirect_uri,
        )

        return mcpo_auth_client_input
