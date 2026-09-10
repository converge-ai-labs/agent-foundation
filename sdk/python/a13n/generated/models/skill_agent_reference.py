from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar

from attrs import define as _attrs_define

T = TypeVar("T", bound="SkillAgentReference")


@_attrs_define(repr=False)
class SkillAgentReference:
    """
    Attributes:
        agent_id (str):
        agent_key (str):
        agent_name (str):
        agent_revision_id (str):
    """

    agent_id: str
    agent_key: str
    agent_name: str
    agent_revision_id: str

    def to_dict(self) -> dict[str, Any]:
        agent_id = self.agent_id

        agent_key = self.agent_key

        agent_name = self.agent_name

        agent_revision_id = self.agent_revision_id

        field_dict: dict[str, Any] = {}

        field_dict.update(
            {
                "agent_id": agent_id,
                "agent_key": agent_key,
                "agent_name": agent_name,
                "agent_revision_id": agent_revision_id,
            }
        )

        return field_dict

    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        agent_id = d.pop("agent_id")

        agent_key = d.pop("agent_key")

        agent_name = d.pop("agent_name")

        agent_revision_id = d.pop("agent_revision_id")

        skill_agent_reference = cls(
            agent_id=agent_id,
            agent_key=agent_key,
            agent_name=agent_name,
            agent_revision_id=agent_revision_id,
        )

        return skill_agent_reference
