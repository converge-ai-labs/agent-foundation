from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

from ..models.mcpo_auth_discovery_token_endpoint_auth_methods_supported_item import (
    MCPOAuthDiscoveryTokenEndpointAuthMethodsSupportedItem,
)

T = TypeVar("T", bound="MCPOAuthDiscovery")


@_attrs_define(repr=False)
class MCPOAuthDiscovery:
    """
    Attributes:
        authorization_response_iss_parameter_supported (bool):
        issuer_url (str):
        redirect_uri (str):
        token_endpoint_auth_methods_supported (list[MCPOAuthDiscoveryTokenEndpointAuthMethodsSupportedItem]):
    """

    authorization_response_iss_parameter_supported: bool
    issuer_url: str
    redirect_uri: str
    token_endpoint_auth_methods_supported: list[MCPOAuthDiscoveryTokenEndpointAuthMethodsSupportedItem]

    def to_dict(self) -> dict[str, Any]:
        authorization_response_iss_parameter_supported = self.authorization_response_iss_parameter_supported

        issuer_url = self.issuer_url

        redirect_uri = self.redirect_uri

        token_endpoint_auth_methods_supported = []
        for token_endpoint_auth_methods_supported_item_data in self.token_endpoint_auth_methods_supported:
            token_endpoint_auth_methods_supported_item = token_endpoint_auth_methods_supported_item_data.value
            token_endpoint_auth_methods_supported.append(token_endpoint_auth_methods_supported_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "authorization_response_iss_parameter_supported": authorization_response_iss_parameter_supported,
                "issuer_url": issuer_url,
                "redirect_uri": redirect_uri,
                "token_endpoint_auth_methods_supported": token_endpoint_auth_methods_supported,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        authorization_response_iss_parameter_supported = d.pop("authorization_response_iss_parameter_supported")

        issuer_url = d.pop("issuer_url")

        redirect_uri = d.pop("redirect_uri")

        token_endpoint_auth_methods_supported = []
        _token_endpoint_auth_methods_supported = d.pop("token_endpoint_auth_methods_supported")
        for token_endpoint_auth_methods_supported_item_data in _token_endpoint_auth_methods_supported:
            token_endpoint_auth_methods_supported_item = MCPOAuthDiscoveryTokenEndpointAuthMethodsSupportedItem(
                token_endpoint_auth_methods_supported_item_data
            )

            token_endpoint_auth_methods_supported.append(token_endpoint_auth_methods_supported_item)

        mcpo_auth_discovery = cls(
            authorization_response_iss_parameter_supported=authorization_response_iss_parameter_supported,
            issuer_url=issuer_url,
            redirect_uri=redirect_uri,
            token_endpoint_auth_methods_supported=token_endpoint_auth_methods_supported,
        )

        return mcpo_auth_discovery
