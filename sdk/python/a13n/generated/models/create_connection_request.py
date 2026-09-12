from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.connector_source import ConnectorSource
    from ..models.mcp_source import MCPSource


T = TypeVar("T", bound="CreateConnectionRequest")


@_attrs_define(repr=False)
class CreateConnectionRequest:
    """
    Attributes:
        name (str):
        source (ConnectorSource | MCPSource):
    """

    name: str
    source: ConnectorSource | MCPSource

    def to_dict(self) -> dict[str, Any]:
        from ..models.connector_source import ConnectorSource

        name = self.name

        source: dict[str, Any]
        if isinstance(self.source, ConnectorSource):
            source = self.source.to_dict()
        else:
            source = self.source.to_dict()

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "name": name,
                "source": source,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.connector_source import ConnectorSource
        from ..models.mcp_source import MCPSource

        d = dict(src_dict)
        name = d.pop("name")

        def _parse_source(data: object) -> ConnectorSource | MCPSource:
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                source_type_0 = ConnectorSource.from_dict(data)

                return source_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            if not isinstance(data, dict):
                raise TypeError()
            source_type_1 = MCPSource.from_dict(data)

            return source_type_1

        source = _parse_source(d.pop("source"))

        create_connection_request = cls(
            name=name,
            source=source,
        )

        return create_connection_request
