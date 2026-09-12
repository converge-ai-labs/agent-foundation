from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.authorization_action_type import AuthorizationActionType
from ..types import UNSET, Unset

T = TypeVar("T", bound="AuthorizationAction")


@_attrs_define(repr=False)
class AuthorizationAction:
    """
    Attributes:
        type_ (AuthorizationActionType):
        client_registration (None | str | Unset):
        grant_types (list[str] | Unset):
        issuer_url (None | str | Unset):
        redirect_uri (None | str | Unset):
        token_endpoint_auth_methods (list[str] | Unset):
        url (None | str | Unset):
    """

    type_: AuthorizationActionType
    client_registration: str | Unset | None = UNSET
    grant_types: list[str] | Unset = UNSET
    issuer_url: str | Unset | None = UNSET
    redirect_uri: str | Unset | None = UNSET
    token_endpoint_auth_methods: list[str] | Unset = UNSET
    url: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        type_ = self.type_.value

        client_registration: str | Unset | None
        if isinstance(self.client_registration, Unset):
            client_registration = UNSET
        else:
            client_registration = self.client_registration

        grant_types: list[str] | Unset = UNSET
        if not isinstance(self.grant_types, Unset):
            grant_types = self.grant_types

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
            token_endpoint_auth_methods = self.token_endpoint_auth_methods

        url: str | Unset | None
        if isinstance(self.url, Unset):
            url = UNSET
        else:
            url = self.url

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "type": type_,
            }
        )
        if client_registration is not UNSET:
            field_dict["client_registration"] = client_registration
        if grant_types is not UNSET:
            field_dict["grant_types"] = grant_types
        if issuer_url is not UNSET:
            field_dict["issuer_url"] = issuer_url
        if redirect_uri is not UNSET:
            field_dict["redirect_uri"] = redirect_uri
        if token_endpoint_auth_methods is not UNSET:
            field_dict["token_endpoint_auth_methods"] = token_endpoint_auth_methods
        if url is not UNSET:
            field_dict["url"] = url

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        type_ = AuthorizationActionType(d.pop("type"))

        def _parse_client_registration(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        client_registration = _parse_client_registration(d.pop("client_registration", UNSET))

        grant_types = cast(list[str], d.pop("grant_types", UNSET))

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

        token_endpoint_auth_methods = cast(list[str], d.pop("token_endpoint_auth_methods", UNSET))

        def _parse_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        url = _parse_url(d.pop("url", UNSET))

        authorization_action = cls(
            type_=type_,
            client_registration=client_registration,
            grant_types=grant_types,
            issuer_url=issuer_url,
            redirect_uri=redirect_uri,
            token_endpoint_auth_methods=token_endpoint_auth_methods,
            url=url,
        )

        return authorization_action
