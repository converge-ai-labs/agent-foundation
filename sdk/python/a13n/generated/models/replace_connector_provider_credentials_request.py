from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.replace_connector_provider_credentials_request_credentials import (
        ReplaceConnectorProviderCredentialsRequestCredentials,
    )


T = TypeVar("T", bound="ReplaceConnectorProviderCredentialsRequest")


@_attrs_define(repr=False)
class ReplaceConnectorProviderCredentialsRequest:
    """
    Attributes:
        credentials (ReplaceConnectorProviderCredentialsRequestCredentials):
        expected_version (int):
    """

    credentials: ReplaceConnectorProviderCredentialsRequestCredentials
    expected_version: int

    def to_dict(self) -> dict[str, Any]:
        credentials = self.credentials.to_dict()

        expected_version = self.expected_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "credentials": credentials,
                "expected_version": expected_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.replace_connector_provider_credentials_request_credentials import (
            ReplaceConnectorProviderCredentialsRequestCredentials,
        )

        d = dict(src_dict)
        credentials = ReplaceConnectorProviderCredentialsRequestCredentials.from_dict(d.pop("credentials"))

        expected_version = d.pop("expected_version")

        replace_connector_provider_credentials_request = cls(
            credentials=credentials,
            expected_version=expected_version,
        )

        return replace_connector_provider_credentials_request
