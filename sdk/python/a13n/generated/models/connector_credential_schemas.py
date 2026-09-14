from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define
from attrs import field as _attrs_field

if TYPE_CHECKING:
    from ..models.connector_credential_schemas_additional_property import ConnectorCredentialSchemasAdditionalProperty


T = TypeVar("T", bound="ConnectorCredentialSchemas")


@_attrs_define(repr=False)
class ConnectorCredentialSchemas:
    additional_properties: dict[str, ConnectorCredentialSchemasAdditionalProperty] = _attrs_field(
        init=False, factory=dict
    )

    def to_dict(self) -> dict[str, Any]:

        field_dict: dict[str, Any] = {}
        for prop_name, prop in self.additional_properties.items():
            field_dict[prop_name] = prop.to_dict()

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_credential_schemas_additional_property import (
            ConnectorCredentialSchemasAdditionalProperty,
        )

        d = dict(src_dict)
        connector_credential_schemas = cls()

        additional_properties = {}
        for prop_name, prop_dict in d.items():
            additional_property = ConnectorCredentialSchemasAdditionalProperty.from_dict(prop_dict)

            additional_properties[prop_name] = additional_property

        connector_credential_schemas.additional_properties = additional_properties
        return connector_credential_schemas

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> ConnectorCredentialSchemasAdditionalProperty:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: ConnectorCredentialSchemasAdditionalProperty) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
