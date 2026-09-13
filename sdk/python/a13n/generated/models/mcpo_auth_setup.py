from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast

from attrs import define as _attrs_define

from ..types import UNSET, Unset

if TYPE_CHECKING:
    from ..models.mcpo_auth_client_configuration import MCPOAuthClientConfiguration
    from ..models.mcpo_auth_setup_action import MCPOAuthSetupAction


T = TypeVar("T", bound="MCPOAuthSetup")


@_attrs_define(repr=False)
class MCPOAuthSetup:
    """
    Attributes:
        next_action (MCPOAuthSetupAction):
        client (MCPOAuthClientConfiguration | None | Unset):
    """

    next_action: MCPOAuthSetupAction
    client: MCPOAuthClientConfiguration | Unset | None = UNSET

    def to_dict(self) -> dict[str, Any]:
        from ..models.mcpo_auth_client_configuration import MCPOAuthClientConfiguration

        next_action = self.next_action.to_dict()

        client: dict[str, Any] | Unset | None
        if isinstance(self.client, Unset):
            client = UNSET
        elif isinstance(self.client, MCPOAuthClientConfiguration):
            client = self.client.to_dict()
        else:
            client = self.client

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "next_action": next_action,
            }
        )
        if client is not UNSET:
            field_dict["client"] = client

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.mcpo_auth_client_configuration import MCPOAuthClientConfiguration
        from ..models.mcpo_auth_setup_action import MCPOAuthSetupAction

        d = dict(src_dict)
        next_action = MCPOAuthSetupAction.from_dict(d.pop("next_action"))

        def _parse_client(data: object) -> MCPOAuthClientConfiguration | Unset | None:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                client_type_0 = MCPOAuthClientConfiguration.from_dict(data)

                return client_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(MCPOAuthClientConfiguration | Unset | None, data)

        client = _parse_client(d.pop("client", UNSET))

        mcpo_auth_setup = cls(
            next_action=next_action,
            client=client,
        )

        return mcpo_auth_setup
