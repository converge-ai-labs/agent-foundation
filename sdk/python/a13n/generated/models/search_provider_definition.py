from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.search_provider_definition_configuration_schema import SearchProviderDefinitionConfigurationSchema
    from ..models.search_provider_definition_credential_schema import SearchProviderDefinitionCredentialSchema


T = TypeVar("T", bound="SearchProviderDefinition")


@_attrs_define(repr=False)
class SearchProviderDefinition:
    """
    Attributes:
        configuration_schema (SearchProviderDefinitionConfigurationSchema):
        credential_schema (SearchProviderDefinitionCredentialSchema):
        display_name (str):
        setup_url (str):
        type_ (str):
        credential_required (bool | Unset):
    """

    configuration_schema: SearchProviderDefinitionConfigurationSchema
    credential_schema: SearchProviderDefinitionCredentialSchema
    display_name: str
    setup_url: str
    type_: str
    credential_required: bool | Unset = UNSET

    def to_dict(self) -> dict[str, Any]:
        configuration_schema = self.configuration_schema.to_dict()

        credential_schema = self.credential_schema.to_dict()

        display_name = self.display_name

        setup_url = self.setup_url

        type_ = self.type_

        credential_required = self.credential_required

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "configuration_schema": configuration_schema,
                "credential_schema": credential_schema,
                "display_name": display_name,
                "setup_url": setup_url,
                "type": type_,
            }
        )
        if credential_required is not UNSET:
            field_dict["credential_required"] = credential_required

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.search_provider_definition_configuration_schema import (
            SearchProviderDefinitionConfigurationSchema,
        )
        from ..models.search_provider_definition_credential_schema import (
            SearchProviderDefinitionCredentialSchema,
        )

        d = dict(src_dict)
        configuration_schema = SearchProviderDefinitionConfigurationSchema.from_dict(d.pop("configuration_schema"))

        credential_schema = SearchProviderDefinitionCredentialSchema.from_dict(d.pop("credential_schema"))

        display_name = d.pop("display_name")

        setup_url = d.pop("setup_url")

        type_ = d.pop("type")

        credential_required = d.pop("credential_required", UNSET)

        search_provider_definition = cls(
            configuration_schema=configuration_schema,
            credential_schema=credential_schema,
            display_name=display_name,
            setup_url=setup_url,
            type_=type_,
            credential_required=credential_required,
        )

        return search_provider_definition
