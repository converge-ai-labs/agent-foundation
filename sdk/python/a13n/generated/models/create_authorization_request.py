from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.create_authorization_request_method import CreateAuthorizationRequestMethod
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.create_authorization_request_credentials_type_0 import CreateAuthorizationRequestCredentialsType0
    from ..models.create_authorization_request_options import CreateAuthorizationRequestOptions


T = TypeVar("T", bound="CreateAuthorizationRequest")


@_attrs_define(repr=False)
class CreateAuthorizationRequest:
    """
    Attributes:
        expected_version (int):
        method (CreateAuthorizationRequestMethod):
        completion_challenge (None | str | Unset):
        credentials (CreateAuthorizationRequestCredentialsType0 | None | Unset):
        options (CreateAuthorizationRequestOptions | Unset):
        return_url (None | str | Unset):
        state (None | str | Unset):
    """

    expected_version: int
    method: CreateAuthorizationRequestMethod
    completion_challenge: str | Unset | None = UNSET
    credentials: CreateAuthorizationRequestCredentialsType0 | Unset | None = UNSET
    options: CreateAuthorizationRequestOptions | Unset = UNSET
    return_url: str | Unset | None = UNSET
    state: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.create_authorization_request_credentials_type_0 import (
            CreateAuthorizationRequestCredentialsType0,
        )

        expected_version = self.expected_version

        method = self.method.value

        completion_challenge: str | Unset | None
        if isinstance(self.completion_challenge, Unset):
            completion_challenge = UNSET
        else:
            completion_challenge = self.completion_challenge

        credentials: dict[str, Any] | Unset | None
        if isinstance(self.credentials, Unset):
            credentials = UNSET
        elif isinstance(self.credentials, CreateAuthorizationRequestCredentialsType0):
            credentials = self.credentials.to_dict()
        else:
            credentials = self.credentials

        options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.options, Unset):
            options = self.options.to_dict()

        return_url: str | Unset | None
        if isinstance(self.return_url, Unset):
            return_url = UNSET
        else:
            return_url = self.return_url

        state: str | Unset | None
        if isinstance(self.state, Unset):
            state = UNSET
        else:
            state = self.state

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
                "method": method,
            }
        )
        if completion_challenge is not UNSET:
            field_dict["completion_challenge"] = completion_challenge
        if credentials is not UNSET:
            field_dict["credentials"] = credentials
        if options is not UNSET:
            field_dict["options"] = options
        if return_url is not UNSET:
            field_dict["return_url"] = return_url
        if state is not UNSET:
            field_dict["state"] = state

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_authorization_request_credentials_type_0 import (
            CreateAuthorizationRequestCredentialsType0,
        )
        from ..models.create_authorization_request_options import CreateAuthorizationRequestOptions

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        method = CreateAuthorizationRequestMethod(d.pop("method"))

        def _parse_completion_challenge(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        completion_challenge = _parse_completion_challenge(d.pop("completion_challenge", UNSET))

        def _parse_credentials(data: object) -> CreateAuthorizationRequestCredentialsType0 | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credentials_type_0 = CreateAuthorizationRequestCredentialsType0.from_dict(data)

                return credentials_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(CreateAuthorizationRequestCredentialsType0 | Unset | None, data)

        credentials = _parse_credentials(d.pop("credentials", UNSET))

        _options = d.pop("options", UNSET)
        options: CreateAuthorizationRequestOptions | Unset
        if isinstance(_options, Unset):
            options = UNSET
        else:
            options = CreateAuthorizationRequestOptions.from_dict(_options)

        def _parse_return_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        return_url = _parse_return_url(d.pop("return_url", UNSET))

        def _parse_state(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        state = _parse_state(d.pop("state", UNSET))

        create_authorization_request = cls(
            expected_version=expected_version,
            method=method,
            completion_challenge=completion_challenge,
            credentials=credentials,
            options=options,
            return_url=return_url,
            state=state,
        )

        return create_authorization_request
