from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.mcpo_auth_setup_action_client_registration_type_0 import MCPOAuthSetupActionClientRegistrationType0
from ..models.mcpo_auth_setup_action_grant_types_item import MCPOAuthSetupActionGrantTypesItem
from ..models.mcpo_auth_setup_action_token_endpoint_auth_methods_item import (
    MCPOAuthSetupActionTokenEndpointAuthMethodsItem,
)
from ..models.mcpo_auth_setup_action_type import MCPOAuthSetupActionType
from ..types import UNSET, Unset

T = TypeVar("T", bound="MCPOAuthSetupAction")


@_attrs_define(repr=False)
class MCPOAuthSetupAction:
    """
    Attributes:
        type_ (MCPOAuthSetupActionType):
        client_registration (MCPOAuthSetupActionClientRegistrationType0 | None | Unset):
        documentation_url (None | str | Unset):
        grant_types (list[MCPOAuthSetupActionGrantTypesItem] | Unset):
        issuer_url (None | str | Unset):
        redirect_uri (None | str | Unset):
        token_endpoint_auth_methods (list[MCPOAuthSetupActionTokenEndpointAuthMethodsItem] | Unset):
    """

    type_: MCPOAuthSetupActionType
    client_registration: MCPOAuthSetupActionClientRegistrationType0 | Unset | None = UNSET
    documentation_url: str | Unset | None = UNSET
    grant_types: list[MCPOAuthSetupActionGrantTypesItem] | Unset = UNSET
    issuer_url: str | Unset | None = UNSET
    redirect_uri: str | Unset | None = UNSET
    token_endpoint_auth_methods: list[MCPOAuthSetupActionTokenEndpointAuthMethodsItem] | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        type_ = self.type_.value

        client_registration: str | Unset | None
        if isinstance(self.client_registration, Unset):
            client_registration = UNSET
        elif isinstance(self.client_registration, MCPOAuthSetupActionClientRegistrationType0):
            client_registration = self.client_registration.value
        else:
            client_registration = self.client_registration

        documentation_url: str | Unset | None
        if isinstance(self.documentation_url, Unset):
            documentation_url = UNSET
        else:
            documentation_url = self.documentation_url

        grant_types: list[str] | Unset = UNSET
        if not isinstance(self.grant_types, Unset):
            grant_types = []
            for grant_types_item_data in self.grant_types:
                grant_types_item = grant_types_item_data.value
                grant_types.append(grant_types_item)

        issuer_url: str | Unset | None
        if isinstance(self.issuer_url, Unset):
            issuer_url = UNSET
        else:
            issuer_url = self.issuer_url

        redirect_uri: str | Unset | None
        if isinstance(self.redirect_uri, Unset):
            redirect_uri = UNSET
        else:
            redirect_uri = self.redirect_uri

        token_endpoint_auth_methods: list[str] | Unset = UNSET
        if not isinstance(self.token_endpoint_auth_methods, Unset):
            token_endpoint_auth_methods = []
            for token_endpoint_auth_methods_item_data in self.token_endpoint_auth_methods:
                token_endpoint_auth_methods_item = token_endpoint_auth_methods_item_data.value
                token_endpoint_auth_methods.append(token_endpoint_auth_methods_item)

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "type": type_,
            }
        )
        if client_registration is not UNSET:
            field_dict["client_registration"] = client_registration
        if documentation_url is not UNSET:
            field_dict["documentation_url"] = documentation_url
        if grant_types is not UNSET:
            field_dict["grant_types"] = grant_types
        if issuer_url is not UNSET:
            field_dict["issuer_url"] = issuer_url
        if redirect_uri is not UNSET:
            field_dict["redirect_uri"] = redirect_uri
        if token_endpoint_auth_methods is not UNSET:
            field_dict["token_endpoint_auth_methods"] = token_endpoint_auth_methods

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        type_ = MCPOAuthSetupActionType(d.pop("type"))

        def _parse_client_registration(data: object) -> MCPOAuthSetupActionClientRegistrationType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                client_registration_type_0 = MCPOAuthSetupActionClientRegistrationType0(data)

                return client_registration_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MCPOAuthSetupActionClientRegistrationType0 | Unset | None, data)

        client_registration = _parse_client_registration(d.pop("client_registration", UNSET))

        def _parse_documentation_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        documentation_url = _parse_documentation_url(d.pop("documentation_url", UNSET))

        _grant_types = d.pop("grant_types", UNSET)
        grant_types: list[MCPOAuthSetupActionGrantTypesItem] | Unset = UNSET
        if _grant_types is not UNSET:
            grant_types = []
            for grant_types_item_data in _grant_types:
                grant_types_item = MCPOAuthSetupActionGrantTypesItem(grant_types_item_data)

                grant_types.append(grant_types_item)

        def _parse_issuer_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        issuer_url = _parse_issuer_url(d.pop("issuer_url", UNSET))

        def _parse_redirect_uri(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        redirect_uri = _parse_redirect_uri(d.pop("redirect_uri", UNSET))

        _token_endpoint_auth_methods = d.pop("token_endpoint_auth_methods", UNSET)
        token_endpoint_auth_methods: list[MCPOAuthSetupActionTokenEndpointAuthMethodsItem] | Unset = UNSET
        if _token_endpoint_auth_methods is not UNSET:
            token_endpoint_auth_methods = []
            for token_endpoint_auth_methods_item_data in _token_endpoint_auth_methods:
                token_endpoint_auth_methods_item = MCPOAuthSetupActionTokenEndpointAuthMethodsItem(
                    token_endpoint_auth_methods_item_data
                )

                token_endpoint_auth_methods.append(token_endpoint_auth_methods_item)

        mcpo_auth_setup_action = cls(
            type_=type_,
            client_registration=client_registration,
            documentation_url=documentation_url,
            grant_types=grant_types,
            issuer_url=issuer_url,
            redirect_uri=redirect_uri,
            token_endpoint_auth_methods=token_endpoint_auth_methods,
        )

        return mcpo_auth_setup_action
