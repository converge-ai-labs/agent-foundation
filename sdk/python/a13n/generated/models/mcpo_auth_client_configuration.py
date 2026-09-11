from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.mcpo_auth_client_configuration_grant_type import MCPOAuthClientConfigurationGrantType
from ..models.mcpo_auth_client_configuration_source import MCPOAuthClientConfigurationSource
from ..models.mcpo_auth_client_configuration_token_endpoint_auth_method import (
    MCPOAuthClientConfigurationTokenEndpointAuthMethod,
)

T = TypeVar("T", bound="MCPOAuthClientConfiguration")


@_attrs_define(repr=False)
class MCPOAuthClientConfiguration:
    """
    Attributes:
        client_id (str):
        grant_type (MCPOAuthClientConfigurationGrantType):
        issuer_url (str):
        source (MCPOAuthClientConfigurationSource):
        token_endpoint_auth_method (MCPOAuthClientConfigurationTokenEndpointAuthMethod):
    """

    client_id: str
    grant_type: MCPOAuthClientConfigurationGrantType
    issuer_url: str
    source: MCPOAuthClientConfigurationSource
    token_endpoint_auth_method: MCPOAuthClientConfigurationTokenEndpointAuthMethod

    def to_dict(self) -> dict[str, Any]:
        client_id = self.client_id

        grant_type = self.grant_type.value

        issuer_url = self.issuer_url

        source = self.source.value

        token_endpoint_auth_method = self.token_endpoint_auth_method.value

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "client_id": client_id,
                "grant_type": grant_type,
                "issuer_url": issuer_url,
                "source": source,
                "token_endpoint_auth_method": token_endpoint_auth_method,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        client_id = d.pop("client_id")

        grant_type = MCPOAuthClientConfigurationGrantType(d.pop("grant_type"))

        issuer_url = d.pop("issuer_url")

        source = MCPOAuthClientConfigurationSource(d.pop("source"))

        token_endpoint_auth_method = MCPOAuthClientConfigurationTokenEndpointAuthMethod(
            d.pop("token_endpoint_auth_method")
        )

        mcpo_auth_client_configuration = cls(
            client_id=client_id,
            grant_type=grant_type,
            issuer_url=issuer_url,
            source=source,
            token_endpoint_auth_method=token_endpoint_auth_method,
        )

        return mcpo_auth_client_configuration
