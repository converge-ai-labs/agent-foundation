from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.mcpo_auth_client_input import MCPOAuthClientInput


T = TypeVar("T", bound="ConfigureMCPOAuthClientRequest")


@_attrs_define(repr=False)
class ConfigureMCPOAuthClientRequest:
    """
    Attributes:
        client (MCPOAuthClientInput | None):
        expected_version (int):
    """

    client: MCPOAuthClientInput | None
    expected_version: int

    def to_dict(self) -> dict[str, Any]:
        from ..models.mcpo_auth_client_input import MCPOAuthClientInput

        client: dict[str, Any] | None
        if isinstance(self.client, MCPOAuthClientInput):
            client = self.client.to_dict()
        else:
            client = self.client

        expected_version = self.expected_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "client": client,
                "expected_version": expected_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.mcpo_auth_client_input import MCPOAuthClientInput

        d = dict(src_dict)

        def _parse_client(data: object) -> MCPOAuthClientInput | None:
            if data is None:
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                client_type_0 = MCPOAuthClientInput.from_dict(data)

                return client_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MCPOAuthClientInput | None, data)

        client = _parse_client(d.pop("client"))

        expected_version = d.pop("expected_version")

        configure_mcpo_auth_client_request = cls(
            client=client,
            expected_version=expected_version,
        )

        return configure_mcpo_auth_client_request
