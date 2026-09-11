from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

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
        allow_missing_issuer (bool | Unset):
        client_secret (None | str | Unset):
    """

    client_id: str
    issuer_url: str
    token_endpoint_auth_method: MCPOAuthClientInputTokenEndpointAuthMethod
    allow_missing_issuer: bool | Unset = UNSET
    client_secret: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        client_id = self.client_id

        issuer_url = self.issuer_url

        token_endpoint_auth_method = self.token_endpoint_auth_method.value

        allow_missing_issuer = self.allow_missing_issuer

        client_secret: str | Unset | None
        if isinstance(self.client_secret, Unset):
            client_secret = UNSET
        else:
            client_secret = self.client_secret

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "client_id": client_id,
                "issuer_url": issuer_url,
                "token_endpoint_auth_method": token_endpoint_auth_method,
            }
        )
        if allow_missing_issuer is not UNSET:
            field_dict["allow_missing_issuer"] = allow_missing_issuer
        if client_secret is not UNSET:
            field_dict["client_secret"] = client_secret

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        client_id = d.pop("client_id")

        issuer_url = d.pop("issuer_url")

        token_endpoint_auth_method = MCPOAuthClientInputTokenEndpointAuthMethod(d.pop("token_endpoint_auth_method"))

        allow_missing_issuer = d.pop("allow_missing_issuer", UNSET)

        def _parse_client_secret(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        client_secret = _parse_client_secret(d.pop("client_secret", UNSET))

        mcpo_auth_client_input = cls(
            client_id=client_id,
            issuer_url=issuer_url,
            token_endpoint_auth_method=token_endpoint_auth_method,
            allow_missing_issuer=allow_missing_issuer,
            client_secret=client_secret,
        )

        return mcpo_auth_client_input
