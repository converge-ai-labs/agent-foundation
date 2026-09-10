from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.create_connector_provider_request_configuration import CreateConnectorProviderRequestConfiguration
    from ..models.create_connector_provider_request_credentials import CreateConnectorProviderRequestCredentials


T = TypeVar("T", bound="CreateConnectorProviderRequest")


@_attrs_define(repr=False)
class CreateConnectorProviderRequest:
    """
    Attributes:
        configuration (CreateConnectorProviderRequestConfiguration):
        credentials (CreateConnectorProviderRequestCredentials):
        name (str):
        type_ (str):
    """

    configuration: CreateConnectorProviderRequestConfiguration
    credentials: CreateConnectorProviderRequestCredentials
    name: str
    type_: str

    def to_dict(self) -> dict[str, Any]:
        configuration = self.configuration.to_dict()

        credentials = self.credentials.to_dict()

        name = self.name

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration": configuration,
                "credentials": credentials,
                "name": name,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.create_connector_provider_request_configuration import (
            CreateConnectorProviderRequestConfiguration,
        )
        from ..models.create_connector_provider_request_credentials import (
            CreateConnectorProviderRequestCredentials,
        )

        d = dict(src_dict)
        configuration = CreateConnectorProviderRequestConfiguration.from_dict(d.pop("configuration"))

        credentials = CreateConnectorProviderRequestCredentials.from_dict(d.pop("credentials"))

        name = d.pop("name")

        type_ = d.pop("type")

        create_connector_provider_request = cls(
            configuration=configuration,
            credentials=credentials,
            name=name,
            type_=type_,
        )

        return create_connector_provider_request
