from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.connector_provider_definition_configuration_schema import (
        ConnectorProviderDefinitionConfigurationSchema,
    )
    from ..models.connector_provider_definition_credential_schema import ConnectorProviderDefinitionCredentialSchema


T = TypeVar("T", bound="ConnectorProviderDefinition")


@_attrs_define(repr=False)
class ConnectorProviderDefinition:
    """
    Attributes:
        configuration_schema (ConnectorProviderDefinitionConfigurationSchema):
        credential_schema (ConnectorProviderDefinitionCredentialSchema):
        display_name (str):
        type_ (str):
    """

    configuration_schema: ConnectorProviderDefinitionConfigurationSchema
    credential_schema: ConnectorProviderDefinitionCredentialSchema
    display_name: str
    type_: str

    def to_dict(self) -> dict[str, Any]:
        configuration_schema = self.configuration_schema.to_dict()

        credential_schema = self.credential_schema.to_dict()

        display_name = self.display_name

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration_schema": configuration_schema,
                "credential_schema": credential_schema,
                "display_name": display_name,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_provider_definition_configuration_schema import (
            ConnectorProviderDefinitionConfigurationSchema,
        )
        from ..models.connector_provider_definition_credential_schema import (
            ConnectorProviderDefinitionCredentialSchema,
        )

        d = dict(src_dict)
        configuration_schema = ConnectorProviderDefinitionConfigurationSchema.from_dict(d.pop("configuration_schema"))

        credential_schema = ConnectorProviderDefinitionCredentialSchema.from_dict(d.pop("credential_schema"))

        display_name = d.pop("display_name")

        type_ = d.pop("type")

        connector_provider_definition = cls(
            configuration_schema=configuration_schema,
            credential_schema=credential_schema,
            display_name=display_name,
            type_=type_,
        )

        return connector_provider_definition
