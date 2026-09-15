from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..models.web_provider_definition_operations_item import WebProviderDefinitionOperationsItem
from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.web_provider_definition_configuration_schema import WebProviderDefinitionConfigurationSchema
    from ..models.web_provider_definition_credential_schema import WebProviderDefinitionCredentialSchema


T = TypeVar("T", bound="WebProviderDefinition")


@_attrs_define(repr=False)
class WebProviderDefinition:
    """
    Attributes:
        configuration_schema (WebProviderDefinitionConfigurationSchema):
        credential_schema (WebProviderDefinitionCredentialSchema):
        display_name (str):
        operations (list[WebProviderDefinitionOperationsItem]):
        setup_url (str):
        type_ (str):
        credential_required (bool | Unset):
        supports_restricted_scrape (bool | Unset):
    """

    configuration_schema: WebProviderDefinitionConfigurationSchema
    credential_schema: WebProviderDefinitionCredentialSchema
    display_name: str
    operations: list[WebProviderDefinitionOperationsItem]
    setup_url: str
    type_: str
    credential_required: bool | Unset = UNSET
    supports_restricted_scrape: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        configuration_schema = self.configuration_schema.to_dict()

        credential_schema = self.credential_schema.to_dict()

        display_name = self.display_name

        operations = []
        for operations_item_data in self.operations:
            operations_item = operations_item_data.value
            operations.append(operations_item)

        setup_url = self.setup_url

        type_ = self.type_

        credential_required = self.credential_required

        supports_restricted_scrape = self.supports_restricted_scrape

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration_schema": configuration_schema,
                "credential_schema": credential_schema,
                "display_name": display_name,
                "operations": operations,
                "setup_url": setup_url,
                "type": type_,
            }
        )
        if credential_required is not UNSET:
            field_dict["credential_required"] = credential_required
        if supports_restricted_scrape is not UNSET:
            field_dict["supports_restricted_scrape"] = supports_restricted_scrape

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.web_provider_definition_configuration_schema import (
            WebProviderDefinitionConfigurationSchema,
        )
        from ..models.web_provider_definition_credential_schema import (
            WebProviderDefinitionCredentialSchema,
        )

        d = dict(src_dict)
        configuration_schema = WebProviderDefinitionConfigurationSchema.from_dict(d.pop("configuration_schema"))

        credential_schema = WebProviderDefinitionCredentialSchema.from_dict(d.pop("credential_schema"))

        display_name = d.pop("display_name")

        operations = []
        _operations = d.pop("operations")
        for operations_item_data in _operations:
            operations_item = WebProviderDefinitionOperationsItem(operations_item_data)

            operations.append(operations_item)

        setup_url = d.pop("setup_url")

        type_ = d.pop("type")

        credential_required = d.pop("credential_required", UNSET)

        supports_restricted_scrape = d.pop("supports_restricted_scrape", UNSET)

        web_provider_definition = cls(
            configuration_schema=configuration_schema,
            credential_schema=credential_schema,
            display_name=display_name,
            operations=operations,
            setup_url=setup_url,
            type_=type_,
            credential_required=credential_required,
            supports_restricted_scrape=supports_restricted_scrape,
        )

        return web_provider_definition
