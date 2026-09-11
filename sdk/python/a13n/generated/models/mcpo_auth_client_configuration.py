from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.mcpo_auth_client_configuration_token_endpoint_auth_method import (
    MCPOAuthClientConfigurationTokenEndpointAuthMethod,
)
from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPOAuthClientConfiguration")


@_attrs_define(repr=False)
class MCPOAuthClientConfiguration:
    """
    Attributes:
        client_id (str):
        issuer_url (str):
        token_endpoint_auth_method (MCPOAuthClientConfigurationTokenEndpointAuthMethod):
        allow_missing_issuer (bool | Unset):
    """

    client_id: str
    issuer_url: str
    token_endpoint_auth_method: MCPOAuthClientConfigurationTokenEndpointAuthMethod
    allow_missing_issuer: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        client_id = self.client_id

        issuer_url = self.issuer_url

        token_endpoint_auth_method = self.token_endpoint_auth_method.value

        allow_missing_issuer = self.allow_missing_issuer

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

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        client_id = d.pop("client_id")

        issuer_url = d.pop("issuer_url")

        token_endpoint_auth_method = MCPOAuthClientConfigurationTokenEndpointAuthMethod(
            d.pop("token_endpoint_auth_method")
        )

        allow_missing_issuer = d.pop("allow_missing_issuer", UNSET)

        mcpo_auth_client_configuration = cls(
            client_id=client_id,
            issuer_url=issuer_url,
            token_endpoint_auth_method=token_endpoint_auth_method,
            allow_missing_issuer=allow_missing_issuer,
        )

        return mcpo_auth_client_configuration
