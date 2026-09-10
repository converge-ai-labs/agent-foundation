from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.environment_provider_definition_configuration_schema import (
        EnvironmentProviderDefinitionConfigurationSchema,
    )
    from ..models.environment_provider_definition_credential_schema_type_0 import (
        EnvironmentProviderDefinitionCredentialSchemaType0,
    )


T = TypeVar("T", bound="EnvironmentProviderDefinition")


@_attrs_define(repr=False)
class EnvironmentProviderDefinition:
    """
    Attributes:
        configuration_schema (EnvironmentProviderDefinitionConfigurationSchema):
        configuration_versions (list[str]):
        credential_schema (EnvironmentProviderDefinitionCredentialSchemaType0 | None):
        display_name (str):
        requires_keepalive (bool):
        supports_destroy (bool):
        supports_managed (bool):
        supports_stop (bool):
        type_ (str):
    """

    configuration_schema: EnvironmentProviderDefinitionConfigurationSchema
    configuration_versions: list[str]
    credential_schema: EnvironmentProviderDefinitionCredentialSchemaType0 | None
    display_name: str
    requires_keepalive: bool
    supports_destroy: bool
    supports_managed: bool
    supports_stop: bool
    type_: str

    def to_dict(self) -> dict[str, Any]:
        from ..models.environment_provider_definition_credential_schema_type_0 import (
            EnvironmentProviderDefinitionCredentialSchemaType0,
        )

        configuration_schema = self.configuration_schema.to_dict()

        configuration_versions = self.configuration_versions

        credential_schema: dict[str, Any] | None
        if isinstance(self.credential_schema, EnvironmentProviderDefinitionCredentialSchemaType0):
            credential_schema = self.credential_schema.to_dict()
        else:
            credential_schema = self.credential_schema

        display_name = self.display_name

        requires_keepalive = self.requires_keepalive

        supports_destroy = self.supports_destroy

        supports_managed = self.supports_managed

        supports_stop = self.supports_stop

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration_schema": configuration_schema,
                "configuration_versions": configuration_versions,
                "credential_schema": credential_schema,
                "display_name": display_name,
                "requires_keepalive": requires_keepalive,
                "supports_destroy": supports_destroy,
                "supports_managed": supports_managed,
                "supports_stop": supports_stop,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.environment_provider_definition_configuration_schema import (
            EnvironmentProviderDefinitionConfigurationSchema,
        )
        from ..models.environment_provider_definition_credential_schema_type_0 import (
            EnvironmentProviderDefinitionCredentialSchemaType0,
        )

        d = dict(src_dict)
        configuration_schema = EnvironmentProviderDefinitionConfigurationSchema.from_dict(d.pop("configuration_schema"))

        configuration_versions = cast(list[str], d.pop("configuration_versions"))

        def _parse_credential_schema(data: object) -> EnvironmentProviderDefinitionCredentialSchemaType0 | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                credential_schema_type_0 = EnvironmentProviderDefinitionCredentialSchemaType0.from_dict(data)

                return credential_schema_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(EnvironmentProviderDefinitionCredentialSchemaType0 | None, data)

        credential_schema = _parse_credential_schema(d.pop("credential_schema"))

        display_name = d.pop("display_name")

        requires_keepalive = d.pop("requires_keepalive")

        supports_destroy = d.pop("supports_destroy")

        supports_managed = d.pop("supports_managed")

        supports_stop = d.pop("supports_stop")

        type_ = d.pop("type")

        environment_provider_definition = cls(
            configuration_schema=configuration_schema,
            configuration_versions=configuration_versions,
            credential_schema=credential_schema,
            display_name=display_name,
            requires_keepalive=requires_keepalive,
            supports_destroy=supports_destroy,
            supports_managed=supports_managed,
            supports_stop=supports_stop,
            type_=type_,
        )

        return environment_provider_definition
