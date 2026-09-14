from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..models.connector_provider_status import ConnectorProviderStatus
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.update_connector_provider_request_credentials_type_0 import (
        UpdateConnectorProviderRequestCredentialsType0,
    )


T = TypeVar("T", bound="UpdateConnectorProviderRequest")


@_attrs_define(repr=False)
class UpdateConnectorProviderRequest:
    """
    Attributes:
        expected_version (int):
        credentials (None | Unset | UpdateConnectorProviderRequestCredentialsType0):
        name (None | str | Unset):
        status (ConnectorProviderStatus | None | Unset):
    """

    expected_version: int
    credentials: Unset | UpdateConnectorProviderRequestCredentialsType0 | None = UNSET
    name: str | Unset | None = UNSET
    status: ConnectorProviderStatus | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.update_connector_provider_request_credentials_type_0 import (
            UpdateConnectorProviderRequestCredentialsType0,
        )

        expected_version = self.expected_version

        credentials: dict[str, Any] | Unset | None
        if isinstance(self.credentials, Unset):
            credentials = UNSET
        elif isinstance(self.credentials, UpdateConnectorProviderRequestCredentialsType0):
            credentials = self.credentials.to_dict()
        else:
            credentials = self.credentials

        name: str | Unset | None
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name

        status: str | Unset | None
        if isinstance(self.status, Unset):
            status = UNSET
        elif isinstance(self.status, ConnectorProviderStatus):
            status = self.status.value
        else:
            status = self.status

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "expected_version": expected_version,
            }
        )
        if credentials is not UNSET:
            field_dict["credentials"] = credentials
        if name is not UNSET:
            field_dict["name"] = name
        if status is not UNSET:
            field_dict["status"] = status

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.update_connector_provider_request_credentials_type_0 import (
            UpdateConnectorProviderRequestCredentialsType0,
        )

        d = dict(src_dict)
        expected_version = d.pop("expected_version")

        def _parse_credentials(data: object) -> Unset | UpdateConnectorProviderRequestCredentialsType0 | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credentials_type_0 = UpdateConnectorProviderRequestCredentialsType0.from_dict(data)

                return credentials_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(Unset | UpdateConnectorProviderRequestCredentialsType0 | None, data)

        credentials = _parse_credentials(d.pop("credentials", UNSET))

        def _parse_name(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        name = _parse_name(d.pop("name", UNSET))

        def _parse_status(data: object) -> ConnectorProviderStatus | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, str):
                    raise TypeError()
                status_type_0 = ConnectorProviderStatus(data)

                return status_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(ConnectorProviderStatus | Unset | None, data)

        status = _parse_status(d.pop("status", UNSET))

        update_connector_provider_request = cls(
            expected_version=expected_version,
            credentials=credentials,
            name=name,
            status=status,
        )

        return update_connector_provider_request
