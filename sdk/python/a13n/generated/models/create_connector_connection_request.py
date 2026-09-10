from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="CreateConnectorConnectionRequest")


@_attrs_define(repr=False)
class CreateConnectorConnectionRequest:
    """
    Attributes:
        connector_key (str):
        connector_provider_id (str):
        name (str):
    """

    connector_key: str
    connector_provider_id: str
    name: str

    def to_dict(self) -> dict[str, Any]:
        connector_key = self.connector_key

        connector_provider_id = self.connector_provider_id

        name = self.name

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connector_key": connector_key,
                "connector_provider_id": connector_provider_id,
                "name": name,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        connector_key = d.pop("connector_key")

        connector_provider_id = d.pop("connector_provider_id")

        name = d.pop("name")

        create_connector_connection_request = cls(
            connector_key=connector_key,
            connector_provider_id=connector_provider_id,
            name=name,
        )

        return create_connector_connection_request
