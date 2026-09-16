from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, TypeVar

from attrs import define as _attrs_define

if TYPE_CHECKING:
    from ..models.agent_config_output import AgentConfigOutput


T = TypeVar("T", bound="ConfigurationRevisionView")


@_attrs_define(repr=False)
class ConfigurationRevisionView:
    """
    Attributes:
        agent_id (str):
        config (AgentConfigOutput):
        revision_id (str):
        version (int):
    """

    agent_id: str
    config: AgentConfigOutput
    revision_id: str
    version: int

    def to_dict(self) -> dict[str, Any]:
        agent_id = self.agent_id

        config = self.config.to_dict()

        revision_id = self.revision_id

        version = self.version

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_id": agent_id,
                "config": config,
                "revision_id": revision_id,
                "version": version,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.agent_config_output import AgentConfigOutput

        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        config = AgentConfigOutput.from_dict(d.pop("config"))

        revision_id = d.pop("revision_id")

        version = d.pop("version")

        configuration_revision_view = cls(
            agent_id=agent_id,
            config=config,
            revision_id=revision_id,
            version=version,
        )

        return configuration_revision_view
