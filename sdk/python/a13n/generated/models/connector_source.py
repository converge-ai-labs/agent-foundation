from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal, TypeVar, cast

from attrs import define as _attrs_define

T = TypeVar("T", bound="ConnectorSource")


@_attrs_define(repr=False)
class ConnectorSource:
    """
    Attributes:
        connector_key (str):
        kind (Literal['connector']):
        provider_id (str):
    """

    connector_key: str
    kind: Literal["connector"]
    provider_id: str

    def to_dict(self) -> dict[str, Any]:
        connector_key = self.connector_key

        kind = self.kind

        provider_id = self.provider_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "connector_key": connector_key,
                "kind": kind,
                "provider_id": provider_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        connector_key = d.pop("connector_key")

        kind = cast(Literal["connector"], d.pop("kind"))
        if kind != "connector":
            raise ValueError(f"kind must match const 'connector', got '{kind}'")

        provider_id = d.pop("provider_id")

        connector_source = cls(
            connector_key=connector_key,
            kind=kind,
            provider_id=provider_id,
        )

        return connector_source
