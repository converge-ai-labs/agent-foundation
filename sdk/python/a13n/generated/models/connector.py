from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
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
        description (None | str | Unset):
    """

    authentication_methods: list[str]
    connector_provider_id: str
    key: str
    name: str
    setup_schema: ConnectorSetupSchema
    description: str | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        authentication_methods = self.authentication_methods

        connector_provider_id = self.connector_provider_id

        key = self.key

        name = self.name

        setup_schema = self.setup_schema.to_dict()

        description: str | Unset | None
        if isinstance(self.description, Unset):
            description = UNSET
        else:
            description = self.description

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
        if description is not UNSET:
            field_dict["description"] = description

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_setup_schema import ConnectorSetupSchema

        d = dict(src_dict)
        authentication_methods = cast(list[str], d.pop("authentication_methods"))

        connector_provider_id = d.pop("connector_provider_id")

        key = d.pop("key")

        name = d.pop("name")

        setup_schema = ConnectorSetupSchema.from_dict(d.pop("setup_schema"))

        def _parse_description(data: object) -> str | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(str | Unset | None, data)

        description = _parse_description(d.pop("description", UNSET))

        connector = cls(
            authentication_methods=authentication_methods,
            connector_provider_id=connector_provider_id,
            key=key,
            name=name,
            setup_schema=setup_schema,
            description=description,
        )

        return connector
