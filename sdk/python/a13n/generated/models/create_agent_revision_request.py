from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.agent_config_input import AgentConfigInput


T = TypeVar("T", bound="CreateAgentRevisionRequest")


@_attrs_define(repr=False)
class CreateAgentRevisionRequest:
    """
    Attributes:
        config (AgentConfigInput):
        expected_version (int):
    """

    config: AgentConfigInput
    expected_version: int

    def to_dict(self) -> dict[str, Any]:
        config = self.config.to_dict()

        expected_version = self.expected_version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "config": config,
                "expected_version": expected_version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_config_input import AgentConfigInput

        d = dict(src_dict)
        config = AgentConfigInput.from_dict(d.pop("config"))

        expected_version = d.pop("expected_version")

        create_agent_revision_request = cls(
            config=config,
            expected_version=expected_version,
        )

        return create_agent_revision_request
