from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.connector_credential_schemas import ConnectorCredentialSchemas
    from ..models.connector_setup_schema import ConnectorSetupSchema


T = TypeVar("T", bound="Connector")


@_attrs_define(repr=False)
class Connector:
    """
    Attributes:
        authentication_methods (list[str]):
        connector_provider_id (str):
        key (str):
        name (str):
        setup_schema (ConnectorSetupSchema):
        credential_schemas (ConnectorCredentialSchemas | Unset):
        description (None | str | Unset):
        logo_url (None | str | Unset):
        unavailable_reason (None | str | Unset):
    """

    authentication_methods: list[str]
    connector_provider_id: str
    key: str
    name: str
    setup_schema: ConnectorSetupSchema
    credential_schemas: ConnectorCredentialSchemas | Unset = UNSET
    description: str | Unset | None = UNSET
    logo_url: str | Unset | None = UNSET
    unavailable_reason: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        authentication_methods = self.authentication_methods

        connector_provider_id = self.connector_provider_id

        key = self.key

        name = self.name

        setup_schema = self.setup_schema.to_dict()

        credential_schemas: dict[str, Any] | Unset = UNSET
        if not isinstance(self.credential_schemas, Unset):
            credential_schemas = self.credential_schemas.to_dict()

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

        logo_url: str | Unset | None
        if isinstance(self.logo_url, Unset):
            logo_url = UNSET
        else:
            logo_url = self.logo_url

        unavailable_reason: str | Unset | None
        if isinstance(self.unavailable_reason, Unset):
            unavailable_reason = UNSET
        else:
            unavailable_reason = self.unavailable_reason

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "authentication_methods": authentication_methods,
                "connector_provider_id": connector_provider_id,
                "key": key,
                "name": name,
                "setup_schema": setup_schema,
            }
        )
        if credential_schemas is not UNSET:
            field_dict["credential_schemas"] = credential_schemas
        if description is not UNSET:
            field_dict["description"] = description
        if logo_url is not UNSET:
            field_dict["logo_url"] = logo_url
        if unavailable_reason is not UNSET:
            field_dict["unavailable_reason"] = unavailable_reason

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_credential_schemas import ConnectorCredentialSchemas
        from ..models.connector_setup_schema import ConnectorSetupSchema

        d = dict(src_dict)
        authentication_methods = cast(list[str], d.pop("authentication_methods"))

        connector_provider_id = d.pop("connector_provider_id")

        key = d.pop("key")

        name = d.pop("name")

        setup_schema = ConnectorSetupSchema.from_dict(d.pop("setup_schema"))

        _credential_schemas = d.pop("credential_schemas", UNSET)
        credential_schemas: ConnectorCredentialSchemas | Unset
        if isinstance(_credential_schemas, Unset):
            credential_schemas = UNSET
        else:
            credential_schemas = ConnectorCredentialSchemas.from_dict(_credential_schemas)

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        def _parse_logo_url(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        logo_url = _parse_logo_url(d.pop("logo_url", UNSET))

        def _parse_unavailable_reason(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        unavailable_reason = _parse_unavailable_reason(d.pop("unavailable_reason", UNSET))

        connector = cls(
            authentication_methods=authentication_methods,
            connector_provider_id=connector_provider_id,
            key=key,
            name=name,
            setup_schema=setup_schema,
            credential_schemas=credential_schemas,
            description=description,
            logo_url=logo_url,
            unavailable_reason=unavailable_reason,
        )

        return connector
