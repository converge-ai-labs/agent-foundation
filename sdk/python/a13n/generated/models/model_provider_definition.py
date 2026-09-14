from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.model_provider_definition_configuration_schema import ModelProviderDefinitionConfigurationSchema
    from ..models.model_provider_definition_credential_schema import ModelProviderDefinitionCredentialSchema
    from ..models.model_provider_definition_settings_schemas import ModelProviderDefinitionSettingsSchemas


T = TypeVar("T", bound="ModelProviderDefinition")


@_attrs_define(repr=False)
class ModelProviderDefinition:
    """
    Attributes:
        configuration_schema (ModelProviderDefinitionConfigurationSchema):
        credential_schema (ModelProviderDefinitionCredentialSchema):
        default_model_api (str):
        display_name (str):
        settings_schemas (ModelProviderDefinitionSettingsSchemas):
        supported_model_apis (list[str]):
        supports_model_discovery (bool):
        type_ (str):
    """

    configuration_schema: ModelProviderDefinitionConfigurationSchema
    credential_schema: ModelProviderDefinitionCredentialSchema
    default_model_api: str
    display_name: str
    settings_schemas: ModelProviderDefinitionSettingsSchemas
    supported_model_apis: list[str]
    supports_model_discovery: bool
    type_: str

    def to_dict(self) -> dict[str, Any]:
        configuration_schema = self.configuration_schema.to_dict()

        credential_schema = self.credential_schema.to_dict()

        default_model_api = self.default_model_api

        display_name = self.display_name

        settings_schemas = self.settings_schemas.to_dict()

        supported_model_apis = self.supported_model_apis

        supports_model_discovery = self.supports_model_discovery

        type_ = self.type_

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration_schema": configuration_schema,
                "credential_schema": credential_schema,
                "default_model_api": default_model_api,
                "display_name": display_name,
                "settings_schemas": settings_schemas,
                "supported_model_apis": supported_model_apis,
                "supports_model_discovery": supports_model_discovery,
                "type": type_,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.model_provider_definition_configuration_schema import (
            ModelProviderDefinitionConfigurationSchema,
        )
        from ..models.model_provider_definition_credential_schema import (
            ModelProviderDefinitionCredentialSchema,
        )
        from ..models.model_provider_definition_settings_schemas import (
            ModelProviderDefinitionSettingsSchemas,
        )

        d = dict(src_dict)
        configuration_schema = ModelProviderDefinitionConfigurationSchema.from_dict(d.pop("configuration_schema"))

        credential_schema = ModelProviderDefinitionCredentialSchema.from_dict(d.pop("credential_schema"))

        default_model_api = d.pop("default_model_api")

        display_name = d.pop("display_name")

        settings_schemas = ModelProviderDefinitionSettingsSchemas.from_dict(d.pop("settings_schemas"))

        supported_model_apis = cast(list[str], d.pop("supported_model_apis"))

        supports_model_discovery = d.pop("supports_model_discovery")

        type_ = d.pop("type")

        model_provider_definition = cls(
            configuration_schema=configuration_schema,
            credential_schema=credential_schema,
            default_model_api=default_model_api,
            display_name=display_name,
            settings_schemas=settings_schemas,
            supported_model_apis=supported_model_apis,
            supports_model_discovery=supports_model_discovery,
            type_=type_,
        )

        return model_provider_definition
