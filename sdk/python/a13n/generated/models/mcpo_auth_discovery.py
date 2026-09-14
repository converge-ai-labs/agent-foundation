from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.mcpo_auth_discovery_client_registration import MCPOAuthDiscoveryClientRegistration
from ..models.mcpo_auth_discovery_grant_types_supported_item import MCPOAuthDiscoveryGrantTypesSupportedItem
from ..models.mcpo_auth_discovery_token_endpoint_auth_methods_supported_item import (
    MCPOAuthDiscoveryTokenEndpointAuthMethodsSupportedItem,
)

T = TypeVar("T", bound="MCPOAuthDiscovery")


@_attrs_define(repr=False)
class MCPOAuthDiscovery:
    """
    Attributes:
        authorization_response_iss_parameter_supported (bool):
        client_registration (MCPOAuthDiscoveryClientRegistration):
        grant_types_supported (list[MCPOAuthDiscoveryGrantTypesSupportedItem]):
        issuer_url (str):
        redirect_uri (None | str):
        token_endpoint_auth_methods_supported (list[MCPOAuthDiscoveryTokenEndpointAuthMethodsSupportedItem]):
    """

    authorization_response_iss_parameter_supported: bool
    client_registration: MCPOAuthDiscoveryClientRegistration
    grant_types_supported: list[MCPOAuthDiscoveryGrantTypesSupportedItem]
    issuer_url: str
    redirect_uri: str | None
    token_endpoint_auth_methods_supported: list[MCPOAuthDiscoveryTokenEndpointAuthMethodsSupportedItem]

    def to_dict(self) -> dict[str, Any]:
        authorization_response_iss_parameter_supported = self.authorization_response_iss_parameter_supported

        client_registration = self.client_registration.value

        grant_types_supported = []
        for grant_types_supported_item_data in self.grant_types_supported:
            grant_types_supported_item = grant_types_supported_item_data.value
            grant_types_supported.append(grant_types_supported_item)

        issuer_url = self.issuer_url

        redirect_uri: str | None
        redirect_uri = self.redirect_uri

        token_endpoint_auth_methods_supported = []
        for token_endpoint_auth_methods_supported_item_data in self.token_endpoint_auth_methods_supported:
            token_endpoint_auth_methods_supported_item = token_endpoint_auth_methods_supported_item_data.value
            token_endpoint_auth_methods_supported.append(token_endpoint_auth_methods_supported_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "authorization_response_iss_parameter_supported": authorization_response_iss_parameter_supported,
                "client_registration": client_registration,
                "grant_types_supported": grant_types_supported,
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

        client_registration = MCPOAuthDiscoveryClientRegistration(d.pop("client_registration"))

        grant_types_supported = []
        _grant_types_supported = d.pop("grant_types_supported")
        for grant_types_supported_item_data in _grant_types_supported:
            grant_types_supported_item = MCPOAuthDiscoveryGrantTypesSupportedItem(grant_types_supported_item_data)

            grant_types_supported.append(grant_types_supported_item)

        issuer_url = d.pop("issuer_url")

        def _parse_redirect_uri(data: object) -> str | None:
            if data is None:
                return data
            return cast(str | None, data)

        redirect_uri = _parse_redirect_uri(d.pop("redirect_uri"))

        token_endpoint_auth_methods_supported = []
        _token_endpoint_auth_methods_supported = d.pop("token_endpoint_auth_methods_supported")
        for token_endpoint_auth_methods_supported_item_data in _token_endpoint_auth_methods_supported:
            token_endpoint_auth_methods_supported_item = MCPOAuthDiscoveryTokenEndpointAuthMethodsSupportedItem(
                token_endpoint_auth_methods_supported_item_data
            )

            token_endpoint_auth_methods_supported.append(token_endpoint_auth_methods_supported_item)

        mcpo_auth_discovery = cls(
            authorization_response_iss_parameter_supported=authorization_response_iss_parameter_supported,
            client_registration=client_registration,
            grant_types_supported=grant_types_supported,
            issuer_url=issuer_url,
            redirect_uri=redirect_uri,
            token_endpoint_auth_methods_supported=token_endpoint_auth_methods_supported,
        )

        return mcpo_auth_discovery
